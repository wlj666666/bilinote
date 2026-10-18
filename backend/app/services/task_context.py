"""Attempt-local status and retry reporting from managed worker processes."""
import time
from contextvars import ContextVar
from app.services.task_queue import update_attempt

summary_revision = ContextVar('task_summary_revision', default=None)

transcriber_snapshot = ContextVar('task_transcriber_snapshot', default=None)

attempt = ContextVar('task_execution_attempt', default=None)


def status(phase, message=''):
    current = attempt.get()
    if current is None:
        return
    task_id, token = current
    if not update_attempt(task_id, token, phase=phase, message=message):
        raise RuntimeError('任务执行权已失效，停止旧任务')


def retry_wait(seconds, message='临时错误，等待自动重试'):
    current = attempt.get()
    if current:
        update_attempt(*current, phase='WAITING_RETRY', message=message, next_retry_at=time.time() + seconds)
    time.sleep(seconds)
    if current:
        if not update_attempt(*current, phase='SUMMARIZING', message='正在重试模型请求', next_retry_at=None):
            raise RuntimeError('任务执行权已失效')
