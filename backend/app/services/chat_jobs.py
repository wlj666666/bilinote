"""Bounded chat workers for this deployment's single API process.

Only short submit/status requests cross the public tunnel. Results and history
are committed together; an interrupted job is explicitly failed on restart.
"""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from sqlalchemy import select
from openai import APITimeoutError

from app.db import engine as db_module
from app.db.models.chat_jobs import ChatJob
from app.db.models.user_data import ChatEntry
from app.db.models.users import User
from app.services.task_data_lock import task_data_lock
from app.services.task_limits import positive_env
from app.services.user_data import chat_history, require_task
from app.utils.logger import get_logger
from app.utils.secrets import customer_result

logger = get_logger(__name__)
PENDING = ('queued', 'running')


class ChatJobs:
    def __init__(self):
        self._guard = threading.Lock()
        self._pool = None

    def start(self):
        with self._guard:
            if self._pool is not None:
                return
            with db_module.SessionLocal() as db:
                db.query(ChatJob).filter(ChatJob.state.in_(PENDING)).update({
                    'state': 'failed', 'error': '服务已重启，请重新提问', 'finished_at': time.time(),
                }, synchronize_session=False)
                db.commit()
            self._pool = ThreadPoolExecutor(
                max_workers=positive_env('CHAT_MAX_WORKERS', 2), thread_name_prefix='chat')

    def stop(self):
        with self._guard:
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = None

    def submit(self, user_id: int, task_id: str, request_id: str, payload: dict) -> dict:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        with task_data_lock(user_id, task_id), self._guard:
            require_task(task_id, user_id)
            if self._pool is None:
                raise HTTPException(503, '问答服务正在启动，请稍后重试')
            with db_module.SessionLocal() as db:
                # Retain completed jobs for reloads/retries without unbounded growth.
                db.query(ChatJob).filter(ChatJob.finished_at < time.time() - 86400).delete()
                existing = db.scalar(select(ChatJob).where(
                    ChatJob.user_id == user_id, ChatJob.request_id == request_id))
                if existing is not None:
                    if existing.task_id != task_id or existing.payload != encoded:
                        raise HTTPException(409, '请求编号已用于另一个问题')
                    return self._view(existing)
                if db.scalar(select(ChatJob.id).where(
                        ChatJob.user_id == user_id, ChatJob.state.in_(PENDING)).limit(1)):
                    raise HTTPException(409, '已有问题正在处理，请等待回答后再提问')
                if db.query(ChatJob).filter(ChatJob.state.in_(PENDING)).count() >= positive_env('CHAT_QUEUE_LIMIT', 20):
                    raise HTTPException(429, '问答服务繁忙，请稍后重试')
                job = ChatJob(id=str(uuid.uuid4()), user_id=user_id, task_id=task_id,
                              request_id=request_id, payload=encoded, state='queued')
                db.add(job)
                db.commit()
                response = self._view(job)
                self._pool.submit(self._run, job.id)
                return response

    @staticmethod
    def _view(job):
        return {'job_id': job.id, 'task_id': job.task_id, 'status': job.state,
                'result': json.loads(job.result) if job.result else None, 'error': job.error}

    def get(self, job_id: str, user_id: int):
        with db_module.SessionLocal() as db:
            job = db.get(ChatJob, job_id)
            if job is None or job.user_id != user_id:
                raise HTTPException(404, '问答任务不存在')
            task_id, response = job.task_id, self._view(job)
        # Do not hold one pool connection while ownership checks need another.
        require_task(task_id, user_id)
        return response

    def active(self, task_id: str, user_id: int):
        require_task(task_id, user_id)
        with db_module.SessionLocal() as db:
            job = db.scalar(select(ChatJob).where(ChatJob.user_id == user_id,
                ChatJob.task_id == task_id, ChatJob.state.in_(PENDING)).order_by(ChatJob.created_at.desc()))
            return self._view(job) if job else None

    def _run(self, job_id: str):
        started = time.monotonic()
        try:
            with db_module.SessionLocal() as db:
                job = db.get(ChatJob, job_id)
                if job is None or job.state != 'queued':
                    return
                user = db.get(User, job.user_id)
                if user is None or not user.enabled:
                    raise ValueError('账号不可用')
                user_id, task_id, admin = user.id, job.task_id, user.role == 'admin'
                remaining = positive_env('CHAT_TIMEOUT_SECONDS', 180) - (time.time() - job.created_at)
                if remaining <= 0:
                    raise TimeoutError('排队等待超时')
                payload = json.loads(job.payload)
                job.state = 'running'
                db.commit()
            require_task(task_id, user_id)
            if not admin:
                from app.services.available_models import require_available_model
                require_available_model(payload['provider_id'], payload['model_name'])
            from app.services.chat_service import chat
            result = chat(task_id=task_id, user_id=user_id, history=chat_history(task_id, user_id),
                          timeout_seconds=remaining, **payload)
            if not admin:
                result = customer_result(result)
            with task_data_lock(user_id, task_id):
                require_task(task_id, user_id)
                with db_module.SessionLocal() as db:
                    job = db.get(ChatJob, job_id)
                    if job is None or job.state != 'running':
                        return
                    for message in [
                        {'role': 'user', 'content': payload['question']},
                        {'role': 'assistant', 'content': result['answer'], 'sources': result.get('sources', [])},
                    ]:
                        db.add(ChatEntry(user_id=user_id, task_id=task_id,
                                         payload=json.dumps(message, ensure_ascii=False)))
                    job.state, job.result = 'completed', json.dumps(result, ensure_ascii=False)
                    job.finished_at = time.time()
                    db.commit()
            logger.info('问答任务完成: job_id=%s elapsed=%.2fs', job_id, time.monotonic() - started)
        except Exception as exc:
            logger.exception('问答任务失败: job_id=%s elapsed=%.2fs', job_id, time.monotonic() - started)
            with db_module.SessionLocal() as db:
                job = db.get(ChatJob, job_id)
                if job is not None:
                    job.state = 'failed'
                    job.error = ('回答超时，请稍后重试或缩小问题范围' if isinstance(exc, (TimeoutError, APITimeoutError))
                                 else '问答失败，请稍后重试或联系管理员')
                    job.finished_at = time.time()
                    db.commit()


chat_jobs = ChatJobs()
