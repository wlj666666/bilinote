"""Small, bounded OS probes. Missing measurements are null, never fabricated zeroes."""
import csv
import io
import math
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from app.db import engine as db_module

_guard = threading.Lock()
_cached = None


def read_number(path):
    value = Path(path).read_text().strip()
    return None if value == 'max' else int(value)


def cpu_ticks():
    # guest time is already included in user/nice; don't count it twice.
    values = [int(value) for value in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    return sum(values), values[3] + values[4]


def linux_memory():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        values[key] = int(value.strip().split()[0]) * 1024
    total = values['MemTotal']
    available = values.get('MemAvailable')
    if available is None:
        raise ValueError('MemAvailable unavailable')
    used = total - available
    scope = 'system'
    # cgroup v2, then v1. Unlimited containers retain system-visible memory.
    for limit_path, usage_path in [
        ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory.current'),
        ('/sys/fs/cgroup/memory/memory.limit_in_bytes', '/sys/fs/cgroup/memory/memory.usage_in_bytes'),
    ]:
        try:
            limit = read_number(limit_path)
            if limit is not None and 0 < limit < total:
                total, used, scope = limit, read_number(usage_path), 'container'
                break
        except (OSError, ValueError):
            continue
    if used is None:
        raise ValueError('Memory usage unavailable')
    return {'status': 'ok', 'used_bytes': used, 'total_bytes': total,
            'percent': round(used / total * 100, 1), 'scope': scope}


def cpu_probe():
    first = cpu_ticks()
    time.sleep(.1)
    second = cpu_ticks()
    delta = second[0] - first[0]
    if delta <= 0:
        raise ValueError('CPU sample unavailable')
    result = {'status': 'ok', 'percent': round(max(0, min(100, (delta - second[1] + first[1]) / delta * 100)), 1),
              'scope': 'system', 'cores': os.cpu_count()}
    # Report container consumption relative to its actual quota when available.
    quota_file = Path('/sys/fs/cgroup/cpu.max')
    if quota_file.exists():
        quota, period = quota_file.read_text().split()
        if quota != 'max':
            cores = int(quota) / int(period)
            if cores <= 0:
                raise ValueError('Invalid CPU quota')
            def usage():
                return int(dict(line.split() for line in Path('/sys/fs/cgroup/cpu.stat').read_text().splitlines())['usage_usec'])
            start, start_at = usage(), time.monotonic()
            time.sleep(.1)
            percent = (usage() - start) / ((time.monotonic() - start_at) * 1_000_000 * cores) * 100
            result.update(percent=round(max(0, min(100, percent)), 1), scope='container', cores=cores)
    return result


def numeric(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except ValueError:
        return None


def gpu_probe():
    binary = shutil.which('nvidia-smi')
    if binary is None and Path('/usr/lib/wsl/lib/nvidia-smi').is_file():
        binary = '/usr/lib/wsl/lib/nvidia-smi'
    if binary is None:
        return {'status': 'unsupported', 'devices': [], 'reason': '未找到 NVIDIA 监测工具，GPU 指标不可用'}
    try:
        response = subprocess.run([binary, '--query-gpu=name,utilization.gpu,memory.used,memory.total',
                                   '--format=csv,noheader,nounits'], capture_output=True, text=True,
                                  timeout=2, check=True)
        devices = []
        for row in csv.reader(io.StringIO(response.stdout)):
            if len(row) != 4:
                continue
            name, utilization, used, total = [value.strip() for value in row]
            used, total = numeric(used), numeric(total)
            devices.append({'name': name, 'percent': numeric(utilization),
                            'used_bytes': used * 1024 ** 2 if used is not None else None,
                            'total_bytes': total * 1024 ** 2 if total is not None else None})
        if not devices:
            return {'status': 'unsupported', 'devices': [], 'reason': '未检测到可读取指标的 NVIDIA GPU'}
        return {'status': 'ok', 'devices': devices, 'scope': 'device'}
    except (OSError, subprocess.SubprocessError):
        return {'status': 'error', 'devices': [], 'reason': 'GPU 采集失败，驱动或设备可能不可用'}


def disk_probe():
    url = db_module.engine.url
    is_sqlite = url.get_backend_name() == 'sqlite' and url.database not in {None, '', ':memory:'}
    path = Path(url.database).resolve().parent if is_sqlite else Path(os.getenv('DATA_DIR', 'data')).resolve()
    # On first launch the configured directory may not have been created yet.
    while not path.exists() and path != path.parent:
        path = path.parent
    total, used, free = shutil.disk_usage(path)
    return {'status': 'ok', 'used_bytes': used, 'total_bytes': total, 'free_bytes': free,
            'percent': round(used / total * 100, 1), 'scope': 'filesystem',
            'label': '数据库所在磁盘' if is_sqlite else '数据目录所在磁盘'}


def resource_snapshot():
    global _cached
    with _guard:
        if _cached and time.monotonic() - _cached[0] < 5:
            return _cached[1]
        snapshot = {'environment': 'container' if Path('/.dockerenv').exists() else 'system'}
        for name, probe in [('cpu', cpu_probe), ('memory', linux_memory), ('gpu', gpu_probe), ('disk', disk_probe)]:
            try:
                snapshot[name] = probe()
            except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError):
                snapshot[name] = {'status': 'unsupported', 'reason': '当前环境无法读取该指标'}
        snapshot['observed_at'] = time.time()
        _cached = time.monotonic(), snapshot
        return snapshot


def service_snapshot():
    from app.services.task_scheduler import task_scheduler
    now = time.time()
    alive = bool(task_scheduler.thread and task_scheduler.thread.is_alive())
    heartbeat = task_scheduler.last_heartbeat_at
    recent = heartbeat is not None and now - heartbeat <= 30
    failed = task_scheduler.last_error_at is not None and (
        task_scheduler.last_cycle_ok_at is None or task_scheduler.last_error_at > task_scheduler.last_cycle_ok_at)
    scheduler_status = 'ok' if alive and recent and not failed else 'error'
    process = task_scheduler.inference
    inference_alive = process is not None and process.poll() is None
    return {
        'observed_at': now, 'backend': {'status': 'ok', 'detail': '当前请求正常响应'},
        'scheduler': {'status': scheduler_status, 'heartbeat_at': heartbeat,
                      'detail': '调度循环正常' if scheduler_status == 'ok' else
                      ('调度循环最近执行失败' if failed else '调度器未运行或心跳已过期')},
        'inference': {'status': 'ok' if inference_alive else 'error',
                      'detail': '推理进程运行中' if inference_alive else '推理进程未运行'},
    }
