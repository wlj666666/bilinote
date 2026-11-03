"""Process-wide gates shared by all task workers on this host.

Acquire all requested slots together; never wait while retaining a partial set.
Nested calls in the same thread reuse an already held resource.
"""
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from app.services.task_limits import TaskLimits, runtime_dir
from app.services.task_process import FileLock

held_resources = ContextVar('task_resources', default=frozenset())
LABELS = {'cpu': 'CPU', 'gpu': 'GPU', 'api': '模型接口'}


@contextmanager
def resources(*names, timeout=None):
    held = held_resources.get()
    needed = sorted(set(names) - held)
    if not needed:
        yield
        return
    # Nested requests that add a resource can deadlock against other combinations.
    if held:
        raise RuntimeError('必须在同一处申请该阶段需要的全部资源')
    limits = TaskLimits.from_env()
    locks = []
    started = time.monotonic()
    from app.services.task_context import attempt, status
    current = attempt.get()
    previous = None
    if current:
        from app.db import engine as db_module
        from app.db.models.task_jobs import TaskJob
        with db_module.SessionLocal() as db:
            job = db.get(TaskJob, current[0])
            previous = (job.phase, job.message)
    announced = False
    try:
        while True:
            if timeout is not None and time.monotonic() - started >= timeout:
                raise TimeoutError('等待计算资源超时，请稍后重试')
            complete = True
            for name in needed:
                found = False
                for index in range(getattr(limits, name)):
                    lock = FileLock(runtime_dir() / f'{name}-{index}.lock')
                    if lock.acquire():
                        locks.append(lock)
                        found = True
                        break
                    lock.close()
                if not found:
                    complete = False
                    break
            if complete:
                break
            for lock in locks:
                lock.close()
            locks.clear()
            if current and not announced:
                status('WAITING_RESOURCE', '等待' + '、'.join(LABELS[n] for n in needed) + '资源')
                announced = True
            time.sleep(.1)
        if announced and previous:
            status(*previous)
        token = held_resources.set(held | set(needed))
        work_started = time.monotonic()
        try:
            yield
        finally:
            held_resources.reset(token)
            from app.utils.logger import get_logger
            get_logger(__name__).info('资源阶段 resources=%s wait_seconds=%.2f run_seconds=%.2f',
                ','.join(needed), work_started - started, time.monotonic() - work_started)
    finally:
        for lock in locks:
            lock.close()


def cpu_stage(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with resources('cpu'):
            return fn(*args, **kwargs)
    return wrapped


def provider_cooldown_key(client):
    import hashlib
    identity = str(getattr(client, 'base_url', '')) + '|' + str(getattr(client, 'api_key', ''))
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


def provider_cooldown_remaining(key):
    import json
    try:
        until = json.loads((runtime_dir() / f'api-cooldown-{key}.json').read_text())['until']
        return max(0, until - time.time())
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def record_provider_cooldown(key, seconds):
    import json
    import os
    import tempfile
    lock = FileLock(runtime_dir() / f'api-cooldown-{key}.lock')
    try:
        while not lock.acquire():
            time.sleep(.01)
        until = time.time() + max(seconds, provider_cooldown_remaining(key))
        with tempfile.NamedTemporaryFile(mode='w', dir=runtime_dir(), delete=False) as temporary:
            json.dump({'until': until}, temporary)
        os.replace(temporary.name, runtime_dir() / f'api-cooldown-{key}.json')
    finally:
        lock.close()
