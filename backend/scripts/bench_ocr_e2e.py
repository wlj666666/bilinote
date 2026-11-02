"""End-to-end OCR-only concurrency benchmark through the real scheduler.

Purpose: answer "how many GPU/CPU slots should OCR-only serving use" using
wall-clock and user-visible latency, not image throughput.

What is real here:
  * the production task scheduler, resource file locks and worker subprocesses
  * the production OCR pipeline (decode -> localize -> ROI extract -> visual
    reuse -> cross-frame merge -> local review -> quality gate)
  * real video inputs of differing duration and resolution
  * real local CPU ONNX embedding/indexing

What is stubbed (to avoid paid APIs):
  * the summary LLM call, replaced by a fixed sleep
  * platform subtitles, only for the `subtitle` task kind

Every run uses an isolated database, runtime dir and output dir, so it never
touches live users or their data.

Usage (from backend):
  bash start-gpu.sh scripts/bench_ocr_e2e.py \
      --output data/ocr_bench_2026-10-31/e2e --gpu-slots 1 --cpu-slots 1 \
      --repeat 3 --videos clip_a.mp4 clip_b.mp4 clip_c.mp4 clip_d.mp4
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import types

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 3)


def gpu_sample():
    result = subprocess.run([
        'nvidia-smi', '--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu',
        '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=4, check=True)
    used, total, utilization, temperature = [
        float(v.strip()) for v in result.stdout.splitlines()[0].split(',')]
    return {'time': time.monotonic(), 'used_mib': used, 'total_mib': total,
            'util_pct': utilization, 'temp_c': temperature}


class Monitor:
    """Sample GPU state; abort only this experiment when a guard trips."""

    def __init__(self):
        self.samples = []
        self.reason = None
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stopped.is_set():
            try:
                sample = gpu_sample()
                self.samples.append(sample)
                if sample['used_mib'] / sample['total_mib'] > .91:
                    self.reason = f"显存超过91%: {sample['used_mib']:.0f}/{sample['total_mib']:.0f} MiB"
                    emit({'guard_stop': self.reason})
                    return
                if sample['temp_c'] >= 85:
                    self.reason = f"温度达到{sample['temp_c']:.0f}C"
                    emit({'guard_stop': self.reason})
                    return
            except Exception as exc:
                emit({'monitor_error': type(exc).__name__})
            self.stopped.wait(.4)

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=5)

    def summary(self):
        if not self.samples:
            return {}
        return {'gpu_peak_mib': max(s['used_mib'] for s in self.samples),
                'gpu_util_mean': round(statistics.mean(s['util_pct'] for s in self.samples), 1),
                'gpu_temp_max': max(s['temp_c'] for s in self.samples),
                'samples': len(self.samples), 'guard_reason': self.reason}


def bench_worker():
    """Task worker entry point: real pipeline, stubbed LLM and platform subtitles."""
    from app.services.task_process import watch_parent
    watch_parent(int(os.environ['TASK_PARENT_PID']))
    from app.services.note import NoteGenerator
    from app.gpt.universal_gpt import UniversalGPT

    class StubCompletions:
        def create(self, **kwargs):
            time.sleep(float(os.environ.get('BENCH_SUMMARY_SECONDS', '1')))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content='# 模拟总结\n仅用于隔离压测，不调用外部模型。'))])

    def gpt(self, *_):
        client = types.SimpleNamespace(
            base_url='benchmark://offline', api_key='not-a-real-key',
            chat=types.SimpleNamespace(completions=StubCompletions()))
        return UniversalGPT(client, 'benchmark-summary')

    NoteGenerator._get_gpt = gpt
    from app.downloaders.local_downloader import LocalDownloader
    original_subtitles = LocalDownloader.download_subtitles
    from app.models.transcriber_model import TranscriptResult, TranscriptSegment

    def subtitles(self, video_url, **kwargs):
        if Path(video_url).name.startswith('subtitle-'):
            return TranscriptResult('zh', '压测模拟已有平台字幕',
                                    [TranscriptSegment(0, 2, '压测模拟已有平台字幕')], {})
        return original_subtitles(self, video_url, **kwargs)

    LocalDownloader.download_subtitles = subtitles
    from app.services import note as note_service
    from app.services.storage_scope import scoped_runtime_dir
    note_service.get_app_dir = lambda label: str(
        scoped_runtime_dir(Path(os.environ['BENCH_DATA_ROOT']) / label))
    from app.services.task_worker import execute
    execute()


def run_trial(args, videos, root):
    os.environ.update(
        DATABASE_URL='sqlite:///' + str(root / 'test.db'),
        TASK_RUNTIME_DIR=str(root / 'runtime'),
        NOTE_OUTPUT_DIR=str(root / 'notes'), UPLOAD_ROOT=str(root / 'uploads'),
        STATIC_ROOT=str(root / 'static'), VECTOR_DB_DIR=str(root / 'vectors'),
        DATA_DIR=str(root / 'data'), BENCH_DATA_ROOT=str(root / 'data'),
        TASK_MAX_WORKERS=str(args.active), TASK_USER_ACTIVE='1',
        TASK_USER_QUEUE_LIMIT=str(max(1, len(videos))), TASK_QUEUE_LIMIT='40',
        TASK_CPU_SLOTS=str(args.cpu_slots), TASK_GPU_SLOTS=str(args.gpu_slots),
        TASK_API_SLOTS='2', EMBEDDING_CPU_THREADS='2', WHISPER_CPU_THREADS='2',
        OCR_THREADS='1', WHISPER_KEEP_DURING_OCR='0',
        TASK_TIMEOUT_SECONDS='3600', TASK_HEARTBEAT_TIMEOUT_SECONDS='180',
        BENCH_SUMMARY_SECONDS=str(args.summary_seconds), OCR_DEVICE='cuda')
    if args.gpu_slots > 1:
        os.environ['TASK_ALLOW_MULTI_GPU_SLOTS'] = '1'
    else:
        os.environ.pop('TASK_ALLOW_MULTI_GPU_SLOTS', None)

    from app.db.init_db import init_db
    from app.db import engine as db_module
    from app.db.models.users import User, AuthSession
    from app.db.models.providers import Provider
    from app.db.models.models import Model
    from app.db.models.task_jobs import TaskJob
    from app.db.models.user_data import UserFile
    from app.services.auth import token_digest
    from app.services.task_scheduler import TaskScheduler
    from app.exceptions.exception_handlers import register_exception_handlers
    from app.routers.note import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    init_db()
    users = []
    with db_module.SessionLocal.begin() as db:
        db.add(Provider(id='bench', name='offline benchmark', logo='', type='openai',
                        api_key='not-a-real-key', base_url='http://example.invalid', enabled=1))
        db.add(Model(provider_id='bench', model_name='benchmark-summary', supports_image_input=False))
        for index, video in enumerate(videos):
            user_id = 2001 + index
            token = os.urandom(24).hex()
            directory = root / 'uploads' / 'users' / str(user_id)
            directory.mkdir(parents=True)
            dest = directory / f'ocr-{index}.mp4'
            shutil.copyfile(video, dest)
            relative = dest.relative_to(root / 'uploads').as_posix()
            db.add(User(id=user_id, username=f'e2e-{user_id}',
                        password_hash='disabled-benchmark-login', role='user', enabled=True))
            db.add(AuthSession(token_hash=token_digest(token), user_id=user_id,
                               expires_at=int(time.time()) + 7200))
            db.add(UserFile(kind='uploads', path=relative, user_id=user_id, task_id=None))
            users.append({'id': user_id, 'token': token, 'url': '/uploads/' + relative,
                          'video': Path(video).name})

    class BenchScheduler(TaskScheduler):
        def spawn(self, module, extra=None):
            if module == 'app.services.task_worker':
                return super().spawn('scripts.bench_ocr_e2e', {**(extra or {}),
                                                              'BILINOTE_BENCH_WORKER': '1'})
            return super().spawn(module, extra)

    scheduler = BenchScheduler()
    app = FastAPI()
    app.include_router(router, prefix='/api')
    register_exception_handlers(app)
    monitor = Monitor()
    monitor.start()
    submit_latencies = []
    poll_latencies = []
    resource_waits = []
    started = time.time()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            accepted = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(users))) as pool:
                def submit(user):
                    begin = time.monotonic()
                    response = client.post(
                        '/api/generate_note',
                        headers={'Authorization': 'Bearer ' + user['token']},
                        json={'video_url': user['url'], 'platform': 'local', 'quality': 'fast',
                              'model_name': 'benchmark-summary', 'provider_id': 'bench',
                              'text_extraction_method': 'ocr',
                              'request_id': f'e2e-{args.gpu_slots}-{args.cpu_slots}-{args.tag}-{user["id"]}'})
                    return user, response, time.monotonic() - begin
                for user, response, latency in pool.map(submit, users):
                    submit_latencies.append(latency)
                    if response.status_code == 200:
                        accepted.append({**user, 'task_id': response.json()['data']['task_id']})
                    else:
                        emit({'submit_failed': response.status_code, 'body': response.text[:200]})
            scheduler.start()
            deadline = time.monotonic() + args.timeout
            last_report = 0.
            max_running = 0
            while True:
                with db_module.SessionLocal() as db:
                    jobs = db.scalars(select(TaskJob)).all()
                    states = {}
                    running = 0
                    for job in jobs:
                        states[job.state] = states.get(job.state, 0) + 1
                        if job.state == 'RUNNING':
                            running += 1
                    max_running = max(max_running, running)
                    terminal = sum(states.get(s, 0) for s in ('SUCCESS', 'FAILED', 'CANCELLED'))
                if time.monotonic() - last_report > 10:
                    emit({'gpu_slots': args.gpu_slots, 'cpu_slots': args.cpu_slots,
                          'repeat': args.tag, 'elapsed': round(time.time() - started, 1),
                          'states': states})
                    last_report = time.monotonic()
                if terminal == len(accepted):
                    break
                if monitor.reason or time.monotonic() > deadline:
                    raise RuntimeError(monitor.reason or '实验超时')
                time.sleep(.5)
            finished = time.time()
            monitor.stop()
            results = []
            with db_module.SessionLocal() as db:
                for user in accepted:
                    job = db.get(TaskJob, user['task_id'])
                    stage_times = json.loads(job.stage_times or '{}')
                    results.append({
                        'video': user['video'], 'state': job.state,
                        'queue_seconds': round((job.started_at or started) - job.created_at, 3),
                        'total_seconds': round((job.finished_at or finished) - job.created_at, 3),
                        'stage_times': stage_times, 'error': job.error_detail})
            scheduler.stop()
            transcripts = {}
            for user in accepted:
                for candidate in (root / 'notes').rglob(f'*{user["task_id"]}_transcript.json'):
                    try:
                        data = json.loads(candidate.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        continue
                    transcripts[user['video']] = {
                        'full_text': data.get('full_text') or '',
                        'segments': [[round(float(s.get('start', 0)), 3),
                                      round(float(s.get('end', 0)), 3),
                                      s.get('text') or ''] for s in (data.get('segments') or [])],
                        'raw_source': (data.get('raw') or {}).get('source'),
                        'quality_status': (data.get('raw') or {}).get('quality_status'),
                        'band_ocr_reads': (data.get('raw') or {}).get('band_ocr_reads'),
                        'visual_reused_frames': (data.get('raw') or {}).get('visual_reused_frames'),
                    }
    finally:
        scheduler.stop()
        monitor.stop()
        db_module.engine.dispose()

    elapsed = finished - started
    succeeded = sum(1 for r in results if r['state'] == 'SUCCESS')
    return {
        'gpu_slots': args.gpu_slots, 'cpu_slots': args.cpu_slots, 'tag': args.tag,
        'videos': [Path(v).name for v in videos], 'jobs': len(accepted),
        'succeeded': succeeded, 'elapsed_seconds': round(elapsed, 3),
        'jobs_per_minute': round(succeeded * 60 / elapsed, 3) if elapsed else None,
        'submit_p95': percentile(submit_latencies, .95),
        'completion_p50': percentile([r['total_seconds'] for r in results], .50),
        'completion_p95': percentile([r['total_seconds'] for r in results], .95),
        'queue_p95': percentile([r['queue_seconds'] for r in results], .95),
        'max_running_observed': max_running,
        'resource_summary': monitor.summary(),
        'jobs_detail': results, 'transcripts': transcripts,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--gpu-slots', type=int, default=1)
    parser.add_argument('--cpu-slots', type=int, default=1)
    parser.add_argument('--active', type=int, default=0,
                        help='TASK_MAX_WORKERS; 0 means len(videos)')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--tag', default='r')
    parser.add_argument('--timeout', type=int, default=3600)
    parser.add_argument('--summary-seconds', type=float, default=1.)
    parser.add_argument('--videos', nargs='+', required=True)
    parser.add_argument('--single', action='store_true',
                        help='run exactly one trial in this process (used internally)')
    args = parser.parse_args()
    if not args.active:
        args.active = len(args.videos)

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    videos = [str(Path(v).resolve()) for v in args.videos]

    # app.db.engine binds DATABASE_URL at import time, so a trial can never be
    # repeated inside one process: the second trial would still point at the
    # first trial's deleted SQLite file. Each repeat therefore runs in its own
    # subprocess, which is also what keeps trials fully isolated.
    if args.single:
        with tempfile.TemporaryDirectory(prefix='bn-e2e-') as temporary:
            result = run_trial(args, videos, Path(temporary))
        (output / f'e2e-g{args.gpu_slots}-c{args.cpu_slots}-{args.tag}.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        emit({k: v for k, v in result.items() if k not in {'jobs_detail', 'transcripts'}})
        return

    for repeat in range(args.repeat):
        tag = f'r{repeat}'
        emit({'trial_start': f'gpu={args.gpu_slots} cpu={args.cpu_slots} {tag}'})
        command = [sys.executable, str(Path(__file__).resolve()),
                   '--output', str(output), '--gpu-slots', str(args.gpu_slots),
                   '--cpu-slots', str(args.cpu_slots), '--active', str(args.active),
                   '--tag', tag, '--timeout', str(args.timeout),
                   '--summary-seconds', str(args.summary_seconds),
                   '--videos', *videos, '--single']
        completed = subprocess.run(command, capture_output=True, text=True)
        for line in completed.stdout.splitlines():
            if line.startswith('{'):
                emit(json.loads(line))
        if completed.returncode:
            emit({'trial_failed': tag, 'stderr': completed.stderr[-1500:]})
            raise SystemExit(completed.returncode)

    runs = [json.loads(p.read_text(encoding='utf-8'))
            for p in sorted(output.glob(f'e2e-g{args.gpu_slots}-c{args.cpu_slots}-r*.json'))]
    if not runs:
        raise SystemExit('没有成功的实验轮次')
    summary = {
        'gpu_slots': args.gpu_slots, 'cpu_slots': args.cpu_slots, 'repeats': len(runs),
        'videos': [Path(v).name for v in videos],
        'elapsed_median': statistics.median(r['elapsed_seconds'] for r in runs),
        'elapsed_all': [r['elapsed_seconds'] for r in runs],
        'jobs_per_minute_median': statistics.median(r['jobs_per_minute'] for r in runs),
        'completion_p50_median': statistics.median(
            r['completion_p50'] for r in runs if r['completion_p50'] is not None),
        'completion_p95_median': statistics.median(
            r['completion_p95'] for r in runs if r['completion_p95'] is not None),
        'gpu_peak_max': max(r['resource_summary'].get('gpu_peak_mib', 0) for r in runs),
        'gpu_util_mean': round(statistics.mean(
            r['resource_summary'].get('gpu_util_mean', 0) for r in runs), 1),
        'max_running_max': max(r['max_running_observed'] for r in runs),
        'succeeded_total': sum(r['succeeded'] for r in runs),
        'jobs_total': sum(r['jobs'] for r in runs),
    }
    (output / f'e2e-g{args.gpu_slots}-c{args.cpu_slots}-summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    emit({'summary': summary})


if __name__ == '__main__':
    if os.getenv('BILINOTE_BENCH_WORKER') == '1':
        bench_worker()
    else:
        main()
