"""Single-host scheduler; codecs and model calls run outside the API process."""
import os
import subprocess
import sys
import threading
import time
from app.db import engine as db_module
from app.db.models.task_jobs import TaskJob
from app.services.task_limits import TaskLimits, runtime_dir, positive_env
from app.services.task_process import FileLock, terminate_tree
from app.services.task_queue import claim, recover_jobs, update_attempt

from app.utils.logger import get_logger
logger = get_logger(__name__)


class TaskScheduler:
    def __init__(self):
        self.limits = TaskLimits.from_env()
        self.stopping = threading.Event()
        self.workers = {}
        self.inference = None
        self.thread = None
        self.lock = None

    def start(self):
        if self.thread and self.thread.is_alive():
            raise RuntimeError('任务调度器已启动')
        self.stopping.clear()
        os.environ['TASK_RUNTIME_DIR'] = str(runtime_dir())
        self.lock = FileLock(runtime_dir() / 'scheduler.lock')
        deadline = time.monotonic() + 10
        while not self.lock.acquire():
            if time.monotonic() >= deadline:
                self.lock.close()
                raise RuntimeError('已有任务调度器运行；同一数据目录只能启动一个后端调度器')
            time.sleep(.1)
        try:
            recover_jobs()
            os.environ['TASK_INFERENCE_KEY'] = os.urandom(32).hex()
            self.start_inference()
            self.thread = threading.Thread(target=self.run, name='task-scheduler', daemon=True)
            self.thread.start()
            logger.info('任务调度器已启动 limits=%s', self.limits)
        except BaseException:
            self.stop()
            raise

    def spawn(self, module, extra=None):
        env = {**os.environ, 'TASK_PARENT_PID': str(os.getpid()),
               'OMP_NUM_THREADS': str(positive_env('WHISPER_CPU_THREADS', 2)),
               'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', **(extra or {})}
        options = {'start_new_session': True} if os.name == 'posix' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == 'posix':
            # Keep the singleton lock alive until all old children really exit.
            options['pass_fds'] = (self.lock.file.fileno(),)
        return subprocess.Popen([sys.executable, '-m', module], env=env, **options)

    def start_inference(self):
        ready = runtime_dir() / 'inference.json'
        ready.unlink(missing_ok=True)
        self.inference = self.spawn('app.services.inference_runtime')
        deadline = time.monotonic() + 20
        while not ready.exists():
            if self.inference.poll() is not None or time.monotonic() > deadline:
                terminate_tree(self.inference)
                raise RuntimeError('本地推理服务启动失败')
            time.sleep(.1)

    def reap(self):
        now = time.time()
        for task_id, (process, token, started) in list(self.workers.items()):
            with db_module.SessionLocal() as db:
                job = db.get(TaskJob, task_id)
                stale = job and job.heartbeat_at and now - job.heartbeat_at > positive_env('TASK_HEARTBEAT_TIMEOUT_SECONDS', 90)
            timed_out = now - started > positive_env('TASK_TIMEOUT_SECONDS', 7200)
            if process.poll() is None and not stale and not timed_out:
                continue
            terminate_tree(process)
            if job and job.state == 'RUNNING':
                if timed_out or stale:
                    update_attempt(task_id, token, state='FAILED', phase='FAILED',
                        message='任务执行超时，已停止；可以重试并复用缓存',
                        error_type='WorkerTimeout', finished_at=time.time())
                else:
                    self.restart_interrupted(task_id, token, '任务进程异常退出，正在从缓存恢复')
                # A killed caller may have left inference in progress. Stop its
                # model owner before allowing another GPU stage to begin.
                if job.phase in {'TRANSCRIBING', 'DETECTING_SUBTITLES', 'EXTRACTING_SUBTITLES'}:
                    terminate_tree(self.inference)
            del self.workers[task_id]

    def run(self):
        while not self.stopping.is_set():
            try:
                if self.inference.poll() is not None:
                    # Callers retain their resource gates while the local IPC
                    # client reconnects. Unrelated downloads/API requests continue.
                    terminate_tree(self.inference)
                    self.start_inference()
                self.reap()
                if self.inference.poll() is not None:
                    continue
                while len(self.workers) < self.limits.active and not self.stopping.is_set():
                    item = claim(self.limits)
                    if item is None:
                        break
                    task_id, token = item
                    try:
                        process = self.spawn('app.services.task_worker', {'TASK_JOB_ID': task_id, 'TASK_ATTEMPT_TOKEN': token})
                        self.workers[task_id] = (process, token, time.time())
                        update_attempt(task_id, token, worker_pid=process.pid)
                    except Exception:
                        update_attempt(task_id, token, state='FAILED', phase='FAILED', message='任务进程启动失败，请重试', finished_at=time.time())
                        logger.exception('启动任务失败 task_id=%s', task_id)
            except Exception:
                logger.exception('任务调度异常，将继续重试')
            self.stopping.wait(.25)

    def restart_interrupted(self, task_id, token, message):
        # Retain the workflow reservation; retrying must not overfill a full queue.
        from app.services.task_queue import transaction
        with transaction() as db:
            job = db.get(TaskJob, task_id)
            if job is None or job.state != 'RUNNING' or job.attempt_token != token:
                return
            job.recoveries += 1
            job.worker_pid = None
            job.attempt_token = None
            job.heartbeat_at = None
            job.updated_at = time.time()
            job.error_type = 'WorkerInterrupted'
            if job.recoveries > 2:
                job.state, job.phase, job.message = 'FAILED', 'FAILED', '任务多次异常中断，请检查运行环境后重试'
                job.finished_at = time.time()
            else:
                job.phase, job.message = 'WAITING_RETRY', message

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join()
        for process, _, _ in self.workers.values():
            terminate_tree(process)
        self.workers.clear()
        if self.inference:
            terminate_tree(self.inference)
        # RUNNING rows remain recoverable, with all children stopped first.
        if self.lock:
            self.lock.close()
        self.thread = self.inference = self.lock = None


task_scheduler = TaskScheduler()
