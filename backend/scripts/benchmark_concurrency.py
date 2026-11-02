"""Offline capacity experiments. Never mutates live users or calls paid APIs.

Run from backend with start-gpu.sh. Each experiment is a fresh subprocess with
an isolated database/runtime. GPU suite uses real local speech and local models.
"""
import argparse
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import types

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
OUTPUT = BACKEND / 'data' / 'concurrency_benchmark'
LIVE_DB = BACKEND / 'bili_note.db'


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def memory_info():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        values[key] = int(value.split()[0]) * 1024
    return values


def live_busy():
    if not LIVE_DB.exists():
        return False
    with sqlite3.connect(f'file:{LIVE_DB}?mode=ro', uri=True, timeout=1) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='task_jobs'").fetchone():
            return False
        return bool(db.execute("SELECT COUNT(*) FROM task_jobs WHERE state IN ('RUNNING','QUEUED')").fetchone()[0])


def gpu_sample():
    value = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu',
        '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=4, check=True)
    used, total, utilization, temperature = [float(x.strip()) for x in value.stdout.splitlines()[0].split(',')]
    return {'gpu_used_mib': used, 'gpu_total_mib': total, 'gpu_util_pct': utilization, 'gpu_temp_c': temperature}


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * p
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return round(values[low] + (values[high] - values[low]) * (position - low), 3)


class Monitor:
    """Sample host resources and preserve headroom; abort only this experiment."""
    def __init__(self):
        self.samples = []
        self.stop_event = threading.Event()
        self.reason = None
        self.thread = None
        self.previous_cpu = None
        self.low_memory_count = 0

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            try:
                sample = gpu_sample()
                memory = memory_info()
                cpu = [int(n) for n in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
                total, idle = sum(cpu[:8]), cpu[3] + cpu[4]
                cpu_pct = 0
                if self.previous_cpu:
                    elapsed = total - self.previous_cpu[0]
                    cpu_pct = 100 * (1 - (idle - self.previous_cpu[1]) / max(1, elapsed))
                self.previous_cpu = (total, idle)
                sample.update(time=time.time(), ram_available_mib=memory['MemAvailable']/2**20,
                    cpu_host_pct=round(cpu_pct, 2), swap_used_mib=(memory['SwapTotal']-memory['SwapFree'])/2**20)
                self.samples.append(sample)
                self.low_memory_count = self.low_memory_count + 1 if sample['ram_available_mib'] < 2500 else 0
                if sample['gpu_used_mib'] > .91 * sample['gpu_total_mib']:
                    self.reason = 'GPU memory headroom fell below 9%'
                elif self.low_memory_count >= 3:
                    self.reason = 'available host RAM stayed below 2500 MiB'
                elif sample['gpu_temp_c'] >= 86:
                    self.reason = 'GPU temperature reached 86 C'
                elif live_busy():
                    self.reason = 'live customer task arrived; experiment paused to protect service'
                if self.reason:
                    emit({'guard_stop': self.reason})
                    return
            except Exception as exc:
                emit({'monitor_error': type(exc).__name__})
            self.stop_event.wait(.4)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def summary(self):
        if not self.samples:
            return {}
        return {
            'gpu_peak_mib': max(x['gpu_used_mib'] for x in self.samples),
            'gpu_util_mean_pct': round(statistics.mean(x['gpu_util_pct'] for x in self.samples), 1),
            'gpu_temp_peak_c': max(x['gpu_temp_c'] for x in self.samples),
            'ram_available_min_mib': round(min(x['ram_available_mib'] for x in self.samples)),
            'cpu_host_mean_pct': round(statistics.mean(x['cpu_host_pct'] for x in self.samples), 1),
            'cpu_host_peak_pct': max(x['cpu_host_pct'] for x in self.samples),
            'swap_used_peak_mib': max(x['swap_used_mib'] for x in self.samples),
            'sample_count': len(self.samples), 'guard_reason': self.reason,
        }


def prepare_inputs(seconds=30):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    audio = OUTPUT / f'speech{seconds}.wav'
    if not audio.exists():
        source = BACKEND / 'data/media/914ed34a6c4e2e4c68c6ab24/BV1CuNd6GECx.mp3'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '1', '-ss', '40',
            '-i', str(source), '-t', str(seconds), '-ac', '1', '-ar', '16000', str(audio)], check=True)
    video = OUTPUT / f'speech{seconds}.mp4'
    if not video.exists():
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '1',
            '-f', 'lavfi', '-i', 'color=c=black:s=640x360:r=10', '-i', str(audio),
            '-t', str(seconds), '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', '-c:a', 'aac', str(video)], check=True)
    return audio, video


def gpu_case(args):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['OMP_NUM_THREADS'] = '2'
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
    from app.transcriber.whisper_models import resolve_whisper_model
    audio, _ = prepare_inputs()
    samples = decode_audio(str(audio), sampling_rate=16000)
    assert len(samples) == 30 * 16000
    monitor = Monitor()
    monitor.start()
    started = time.monotonic()
    model = WhisperModel(resolve_whisper_model('large-v3'), device='cuda',
        compute_type=os.getenv('WHISPER_CUDA_COMPUTE_TYPE', 'int8_float32'),
        cpu_threads=2, num_workers=args.active, download_root=str(BACKEND/'models/whisper'))
    load_seconds = time.monotonic() - started
    list(model.transcribe(samples[:5*16000])[0])
    def infer(index):
        begin = time.monotonic()
        segments = list(model.transcribe(samples)[0])
        text = '\n'.join(s.text for s in segments)
        return {'index': index, 'elapsed_seconds': round(time.monotonic()-begin,3),
                'segments': len(segments), 'text_hash': hashlib.sha256(text.encode()).hexdigest()}
    results = []
    errors = []
    begin = time.monotonic()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.active) as pool:
            pending = {pool.submit(infer, index) for index in range(args.jobs)}
            while pending:
                done, pending = concurrent.futures.wait(pending, timeout=.5, return_when=concurrent.futures.FIRST_COMPLETED)
                for item in done:
                    try:
                        results.append(item.result())
                        emit({'gpu_concurrency': args.active, 'completed': len(results), 'jobs': args.jobs,
                              'elapsed_seconds': round(time.monotonic()-begin,1)})
                    except Exception as exc:
                        errors.append({'type':type(exc).__name__,'message':str(exc)[:500]})
                if monitor.reason or errors:
                    for item in pending:
                        item.cancel()
                    # This subprocess owns the entire CUDA context. Parent saves
                    # guard/error metadata and kills the experiment if necessary.
                    if monitor.reason:
                        raise RuntimeError(monitor.reason)
    finally:
        elapsed = time.monotonic() - begin
        monitor.stop()
    report = {'kind':'shared_whisper_gpu','gpu_concurrency':args.active,'jobs':args.jobs,
        'succeeded':len(results),'errors':errors,'elapsed_seconds':round(elapsed,3),
        'jobs_per_minute':round(len(results)*60/elapsed,3),'audio_seconds':30,
        'audio_realtime_multiplier':round(len(results)*30/elapsed,3),
        'latency_p50_seconds':percentile([r['elapsed_seconds'] for r in results],.5),
        'latency_p95_seconds':percentile([r['elapsed_seconds'] for r in results],.95),
        'load_seconds':round(load_seconds,3),'model':'large-v3',
        'compute_type':model.model.compute_type,'resource_summary':monitor.summary(), 'jobs_detail':results}
    model.model.unload_model()
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    emit({'result':str(args.output),**{k:v for k,v in report.items() if k not in {'jobs_detail'}}})


def suite(args):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    prepare_inputs()
    levels = [int(v) for v in args.levels.split(',')]
    summaries = []
    failed = False
    for active in levels:
        if live_busy():
            emit({'stopped':'live service is busy'})
            failed = True
            break
        prefix=args.label or args.suite_kind
        output = OUTPUT / f'{prefix}-{active}.json'
        log = OUTPUT / f'{prefix}-{active}.log'
        command=[sys.executable, str(Path(__file__).resolve()), '--mode', args.suite_kind,
            '--active', str(active), '--jobs', str(args.jobs), '--output', str(output),
            '--summary-seconds',str(args.summary_seconds), '--audio-seconds', str(args.audio_seconds),
            '--ocr-seconds', str(args.ocr_seconds),
            '--gpu-slots', str(args.gpu_slots), '--cpu-slots', str(args.cpu_slots), '--mix', args.mix]
        if args.keep_whisper: command.append('--keep-whisper')
        if args.diverse_ocr: command.append('--diverse-ocr')
        env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
             'OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
             'BENCH_SUITE_PARENT_PID':str(os.getpid())}
        emit({'starting':args.suite_kind,'concurrency':active,'jobs':args.jobs})
        with log.open('w') as stream:
            process=subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            monitor=Monitor();monitor.start()
            deadline=time.monotonic()+args.timeout
            while process.poll() is None:
                if monitor.reason or time.monotonic()>deadline:
                    os.killpg(process.pid,signal.SIGKILL)
                    break
                time.sleep(.5)
            process.wait()
            monitor.stop()
        if output.exists() and process.returncode==0:
            report=json.loads(output.read_text())
            summaries.append(report)
            emit({'finished':active, 'result':str(output),
                  'jobs_per_minute':report.get('jobs_per_minute'), 'resources':report.get('resource_summary')})
        else:
            emit({'failed':active,'exit_code':process.returncode,'guard':monitor.reason,'log':str(log)})
            summaries.append({'concurrency':active,'failed':True,'exit_code':process.returncode,'guard':monitor.reason})
            failed = True
            break
        (OUTPUT/f'{args.label or args.suite_kind}-summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2))
        index_failed = report.get('kind') == 'authenticated_pipeline' and report.get('indexed_count') != report.get('jobs')
        if report.get('errors') or report.get('refused') or report.get('burst',{}).get('other_statuses') or report.get('succeeded',0)<report.get('jobs',0) or index_failed:
            emit({'stopped':'experiment had failed jobs'})
            failed = True
            break
        time.sleep(2)
    (OUTPUT/f'{args.label or args.suite_kind}-summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2))
    if failed:
        raise SystemExit(1)


def bench_worker():
    # This injection exists only in this benchmark entry point, never in serving code.
    from app.services.task_process import watch_parent
    watch_parent(int(os.environ['TASK_PARENT_PID']))
    from app.services.note import NoteGenerator
    from app.gpt.universal_gpt import UniversalGPT
    class StubCompletions:
        def create(self, **kwargs):
            time.sleep(float(os.environ['BENCH_SUMMARY_SECONDS']))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(
                content='# 模拟总结\n此响应仅用于隔离压测，不调用外部模型。'))])
    def gpt(self,*_):
        client=types.SimpleNamespace(base_url='benchmark://offline',api_key='not-a-real-key',
            chat=types.SimpleNamespace(completions=StubCompletions()))
        return UniversalGPT(client,'benchmark-summary')
    NoteGenerator._get_gpt=gpt
    # Real local CPU embedding/indexing is included; cached ONNX weights are required.
    original_subtitles = __import__('app.downloaders.local_downloader', fromlist=['LocalDownloader']).LocalDownloader.download_subtitles
    from app.downloaders.local_downloader import LocalDownloader
    from app.models.transcriber_model import TranscriptResult, TranscriptSegment
    def subtitles(self, video_url, **kwargs):
        if Path(video_url).name.startswith('subtitle-'):
            return TranscriptResult('zh', '压测模拟已有平台字幕，不调用语音转写', [TranscriptSegment(0, 2, '压测模拟已有平台字幕')], {})
        return original_subtitles(self, video_url, **kwargs)
    LocalDownloader.download_subtitles = subtitles
    from app.services import note as note_service
    from app.services.storage_scope import scoped_runtime_dir
    note_service.get_app_dir = lambda label: str(scoped_runtime_dir(Path(os.environ['BENCH_DATA_ROOT'])/label))
    from app.services.task_worker import execute
    execute()


def pipeline_case(args):
    if live_busy():
        raise RuntimeError('Live service has customer tasks')
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    _, speech_video = prepare_inputs(args.audio_seconds)
    names=['portrait_speech','portrait_second','landscape_second','landscape_fourth'] if args.diverse_ocr else ['portrait_speech']
    ocr_videos=[]
    for name in names:
        source=BACKEND/f'data/ocr_validation/v5/clips/{name}-15s.mp4'
        if args.ocr_seconds==15:
            ocr_videos.append(source)
            continue
        target=OUTPUT/f'{name}-{args.ocr_seconds}s.mp4'
        if not target.exists():
            subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-stream_loop','-1','-i',str(source),
                '-t',str(args.ocr_seconds),'-c','copy',str(target)],check=True)
        ocr_videos.append(target)
    with tempfile.TemporaryDirectory(prefix='bn-load-') as temporary:
        root=Path(temporary)
        os.environ.update(DATABASE_URL='sqlite:///'+str(root/'test.db'), TASK_RUNTIME_DIR=str(root/'runtime'),
            NOTE_OUTPUT_DIR=str(root/'notes'), UPLOAD_ROOT=str(root/'uploads'), STATIC_ROOT=str(root/'static'),
            VECTOR_DB_DIR=str(root/'vectors'), DATA_DIR=str(root/'data'), BENCH_DATA_ROOT=str(root/'data'),
            TASK_MAX_WORKERS=str(args.active), TASK_USER_ACTIVE='1', TASK_USER_QUEUE_LIMIT='3', TASK_QUEUE_LIMIT='20',
            TASK_CPU_SLOTS=str(args.cpu_slots), TASK_GPU_SLOTS=str(args.gpu_slots), TASK_API_SLOTS='2', EMBEDDING_CPU_THREADS='2',
            WHISPER_CPU_THREADS='2', OCR_THREADS='1', WHISPER_KEEP_DURING_OCR='1' if args.keep_whisper else '0',
            TASK_TIMEOUT_SECONDS='900', TASK_HEARTBEAT_TIMEOUT_SECONDS='90')
        if args.gpu_slots > 1:
            # Explicit opt-in: the serving code still defaults to a single GPU slot.
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
        from sqlalchemy import select, func
        init_db()
        users=[]
        with db_module.SessionLocal.begin() as db:
            db.add(Provider(id='bench',name='offline benchmark',logo='',type='openai',
                api_key='not-a-real-key',base_url='http://example.invalid',enabled=1))
            db.add(Model(provider_id='bench',model_name='benchmark-summary',supports_image_input=False))
            for index in range(args.jobs):
                user_id=1001+index
                token=os.urandom(24).hex()
                kind=['asr','ocr','subtitle'][index%3] if args.mix == 'full' else 'ocr'
                directory=root/'uploads'/'users'/str(user_id)
                directory.mkdir(parents=True)
                dest=directory/f'{kind}-{index}.mp4'
                shutil.copyfile(ocr_videos[(index//3)%len(ocr_videos)] if kind=='ocr' else speech_video,dest)
                relative=dest.relative_to(root/'uploads').as_posix()
                db.add(User(id=user_id,username=f'load-{user_id}',password_hash='disabled-benchmark-login',role='user',enabled=True))
                db.add(AuthSession(token_hash=token_digest(token),user_id=user_id,expires_at=int(time.time())+3600))
                db.add(UserFile(kind='uploads',path=relative,user_id=user_id,task_id=None))
                users.append({'id':user_id,'token':token,'kind':kind,'url':'/uploads/'+relative})
        class BenchScheduler(TaskScheduler):
            def spawn(self,module,extra=None):
                if module=='app.services.task_worker':
                    return super().spawn('scripts.benchmark_concurrency', {**(extra or {}),
                        'BILINOTE_BENCH_WORKER':'1','BENCH_SUMMARY_SECONDS':str(args.summary_seconds)})
                return super().spawn(module,extra)
        scheduler=BenchScheduler()
        app=FastAPI();app.include_router(router,prefix='/api');register_exception_handlers(app)
        monitor=Monitor();monitor.start()
        accepted=[];refused=[];latencies=[];poll_latencies=[];snapshots=[]
        started=time.time()
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                def submit(user):
                    begin=time.monotonic()
                    response=client.post('/api/generate_note',headers={'Authorization':'Bearer '+user['token']},json={
                        'video_url':user['url'],'platform':'local','quality':'fast',
                        'model_name':'benchmark-summary','provider_id':'bench',
                        'text_extraction_method':'ocr' if user['kind']=='ocr' else 'asr',
                        'request_id':f'load-{args.active}-{user["id"]}'})
                    return user,response,time.monotonic()-begin
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(32,args.jobs)) as pool:
                    for user,response,latency in pool.map(submit,users):
                        latencies.append(latency)
                        if response.status_code==200:
                            accepted.append({**user,'task_id':response.json()['data']['task_id']})
                        else:
                            refused.append({'status':response.status_code,'body':response.text[:300]})
                scheduler.start()
                last_progress=0
                deadline=time.monotonic()+args.timeout
                while True:
                    with db_module.SessionLocal() as db:
                        jobs=db.scalars(select(TaskJob)).all()
                        counts={}
                        per_user={}
                        for job in jobs:
                            counts[job.state]=counts.get(job.state,0)+1
                            if job.state=='RUNNING':per_user[job.user_id]=per_user.get(job.user_id,0)+1
                        assert max(per_user.values(),default=0)<=1
                        assert counts.get('RUNNING',0)<=args.active
                        snapshots.append({'time':time.time(),'states':counts})
                        terminal=sum(counts.get(state,0) for state in ['SUCCESS','FAILED','CANCELLED'])
                    if time.monotonic()-last_progress>5:
                        emit({'pipeline_concurrency':args.active,'elapsed_seconds':round(time.time()-started,1),'states':counts})
                        last_progress=time.monotonic()
                    if terminal==len(accepted):break
                    if monitor.reason or time.monotonic()>deadline:
                        raise RuntimeError(monitor.reason or 'experiment deadline reached')
                    # Exercise authenticated status polling while native work runs.
                    for user in accepted[:min(len(accepted),6)]:
                        begin=time.monotonic()
                        response=client.get('/api/task_status/'+user['task_id'],headers={'Authorization':'Bearer '+user['token']})
                        poll_latencies.append(time.monotonic()-begin)
                        assert response.status_code==200
                    time.sleep(.3)
                finished=time.time()
                monitor.stop()
                job_results=[]
                with db_module.SessionLocal() as db:
                    for user in accepted:
                        job=db.get(TaskJob,user['task_id'])
                        job_results.append({'kind':user['kind'],'state':job.state,'attempts':job.attempts,
                            'queue_seconds':round((job.started_at or started)-job.created_at,3),
                            'total_seconds':round((job.finished_at or finished)-job.created_at,3),
                            'stage_times':json.loads(job.stage_times), 'error_type':job.error_type,'error':job.error_detail})
                scheduler.stop()
                from app.services.vector_store import VectorStoreManager
                indexed_count=sum(VectorStoreManager(user_id=user['id']).is_indexed(user['task_id']) for user in accepted)
                burst_results=[]
                def burst_submit(index):
                    user=users[index%len(users)]
                    begin=time.monotonic()
                    response=client.post('/api/generate_note',headers={'Authorization':'Bearer '+user['token']},json={
                        'video_url':user['url'],'platform':'local','quality':'fast',
                        'model_name':'benchmark-summary','provider_id':'bench',
                        'request_id':f'burst-{index}'})
                    return response.status_code,time.monotonic()-begin
                with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
                    burst_results=list(pool.map(burst_submit,range(80)))
                with db_module.SessionLocal() as db:
                    queue_count=db.scalar(select(func.count()).select_from(TaskJob).where(TaskJob.state=='QUEUED'))
                    per_user_queue=db.execute(select(TaskJob.user_id,func.count()).where(TaskJob.state=='QUEUED').group_by(TaskJob.user_id)).all()
                    assert queue_count<=20
                    assert max((number for _,number in per_user_queue),default=0)<=3
                succeeded=sum(j['state']=='SUCCESS' for j in job_results)
                elapsed=finished-started
                report={'kind':'authenticated_pipeline','active':args.active,'users':args.jobs,'jobs':len(accepted),
                    'succeeded':succeeded,'indexed_count':indexed_count,'refused':refused,'elapsed_seconds':round(elapsed,3),
                    'burst':{'requests':80,'accepted':sum(status==200 for status,_ in burst_results),
                        'rate_limited':sum(status==429 for status,_ in burst_results),
                        'other_statuses':[status for status,_ in burst_results if status not in {200,429}],
                        'p95_seconds':percentile([duration for _,duration in burst_results],.95),'queued_after_burst':queue_count},
                    'jobs_per_minute':round(succeeded*60/elapsed,3),
                    'summary_stub_seconds':args.summary_seconds,'keep_whisper_during_ocr':args.keep_whisper,'gpu_slots':args.gpu_slots,'cpu_slots':args.cpu_slots,'api_slots':2,'mix':args.mix,
                    'local_indexing':'real CPU ONNX embeddings', 'media':{'asr_seconds':args.audio_seconds,'ocr_seconds':args.ocr_seconds,'ocr_profiles':names,'subtitle':'simulated platform subtitle'},
                    'submit_p95_seconds':percentile(latencies,.95),'poll_p95_seconds':percentile(poll_latencies,.95),
                    'queue_p95_seconds':percentile([j['queue_seconds'] for j in job_results],.95),
                    'completion_p50_seconds':percentile([j['total_seconds'] for j in job_results],.5),
                    'completion_p95_seconds':percentile([j['total_seconds'] for j in job_results],.95),
                    'max_active_observed':max((s['states'].get('RUNNING',0) for s in snapshots),default=0),
                    'resource_summary':monitor.summary(),'jobs_detail':job_results,'snapshots':snapshots}
                Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
                emit({'result':str(args.output),**{k:v for k,v in report.items() if k not in {'jobs_detail','snapshots'}}})
        finally:
            scheduler.stop();monitor.stop()
            db_module.engine.dispose()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['suite','gpu','pipeline'],default='suite')
    parser.add_argument('--suite-kind',choices=['gpu','pipeline'],default='gpu')
    parser.add_argument('--levels',default='1,2,3,4')
    parser.add_argument('--active',type=int,default=1)
    parser.add_argument('--jobs',type=int,default=12)
    parser.add_argument('--timeout',type=int,default=600)
    parser.add_argument('--summary-seconds',type=float,default=6)
    parser.add_argument('--audio-seconds',type=int,default=30)
    parser.add_argument('--ocr-seconds',type=int,default=15)
    parser.add_argument('--diverse-ocr',action='store_true')
    parser.add_argument('--keep-whisper',action='store_true')
    parser.add_argument('--gpu-slots',type=int,default=1)
    parser.add_argument('--cpu-slots',type=int,default=2)
    parser.add_argument('--mix',choices=['full','ocr-only'],default='full')
    parser.add_argument('--label',default='')
    parser.add_argument('--output',default=str(OUTPUT/'case.json'))
    args=parser.parse_args()
    if args.mode != 'suite' and os.getenv('BENCH_SUITE_PARENT_PID'):
        parent_pid=int(os.environ['BENCH_SUITE_PARENT_PID'])
        def watch_suite():
            while True:
                if os.getppid()!=parent_pid:
                    os.killpg(os.getpgrp(),signal.SIGKILL)
                time.sleep(.25)
        threading.Thread(target=watch_suite,daemon=True).start()
    if not 1 <= args.audio_seconds <= 360 or not 1 <= args.ocr_seconds <= 1800:
        parser.error('audio-seconds must be 1..360; ocr-seconds must be 1..1800')
    if args.jobs<1 or args.active<1 or args.jobs>100 or args.active>16:
        parser.error('jobs must be 1..100 and active 1..16')
    if args.timeout <= 0 or not math.isfinite(args.summary_seconds) or args.summary_seconds < 0:
        parser.error('timeout must be positive; summary-seconds must be finite and nonnegative')
    if args.mode == 'suite':
        try:
            levels = [int(value) for value in args.levels.split(',')]
        except ValueError:
            parser.error('levels must be comma-separated integers')
        if not levels or any(not 1 <= value <= 16 for value in levels):
            parser.error('each concurrency level must be 1..16')
    if args.label and any(character not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for character in args.label):
        parser.error('label must contain only letters, numbers, underscores or hyphens')
    if args.mode=='suite':suite(args)
    elif args.mode=='gpu':gpu_case(args)
    else:pipeline_case(args)


if __name__=='__main__':
    if os.getenv('BILINOTE_BENCH_WORKER')=='1':bench_worker()
    else:main()
