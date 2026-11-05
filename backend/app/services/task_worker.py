"""One managed task attempt, with independent heartbeat and bounded retries."""
import json
import os
import threading
import time
from app.services.task_process import watch_parent


def execute():
    watch_parent(int(os.environ['TASK_PARENT_PID']))
    from app.db import engine as db_module
    from app.db.models.task_jobs import TaskJob
    from app.services.task_context import attempt, transcriber_snapshot, summary_revision
    from app.services.task_queue import update_attempt
    from app.services.task_limits import positive_env
    from app.utils.secrets import redact_secrets
    task_id, token = os.environ['TASK_JOB_ID'], os.environ['TASK_ATTEMPT_TOKEN']
    with db_module.SessionLocal() as db:
        job = db.get(TaskJob, task_id)
        if job is None or job.state != 'RUNNING' or job.attempt_token != token:
            return
        payload, user_id = json.loads(job.payload), job.user_id
    attempt.set((task_id, token))
    transcriber_snapshot.set(payload['transcriber'])
    summary_revision.set(payload.get('summary_revision'))
    stopped = threading.Event()
    def heartbeat():
        while not stopped.wait(5):
            try:
                if not update_attempt(task_id, token, heartbeat_at=time.time()):
                    os._exit(1)
            except Exception:
                # Scheduler terminates the process if DB writes stay unavailable.
                import logging
                logging.getLogger(__name__).exception('任务心跳写入失败')
    threading.Thread(target=heartbeat, daemon=True).start()
    from app.routers.note import run_note_task, _persist_prefetched_transcript, NOTE_OUTPUT_DIR
    from app.services.storage_scope import task_scope
    from app.services.user_data import require_task, task_notes_dir
    from app.gpt.universal_gpt import UniversalGPT
    from app.enmus.note_enums import DownloadQuality
    form = payload['form']
    task = require_task(task_id, user_id)
    notes_dir = task_notes_dir(task, NOTE_OUTPUT_DIR)
    try:
        prefetched = form.get('prefetched_transcript')
        if prefetched:
            with task_scope(user_id, task_id, notes_dir):
                _persist_prefetched_transcript(task_id, prefetched, payload['source'], form['platform'])
        max_attempts = positive_env('TASK_RETRY_ATTEMPTS', 2)
        for index in range(max_attempts):
            try:
                run_note_task(task_id, payload['source'], form['platform'], DownloadQuality(form['quality']),
                    link=form.get('link', False), screenshot=form.get('screenshot', False), model_name=form['model_name'],
                    provider_id=form['provider_id'], _format=form.get('format'), style=form.get('style'),
                    extras=form.get('extras'), video_understanding=form.get('video_understanding', False),
                    video_interval=form.get('video_interval', 0), grid_size=form.get('grid_size') or [],
                    text_extraction_method=form.get('text_extraction_method', 'asr'), user_id=user_id)
                update_attempt(task_id, token, state='SUCCESS', phase='SUCCESS', message='笔记生成完成',
                    finished_at=time.time(), next_retry_at=None, error_type=None, error_detail=None)
                return
            except Exception as exc:
                error = redact_secrets(str(exc))[:4000]
                if index + 1 == max_attempts or not UniversalGPT._is_retryable_error(exc):
                    update_attempt(task_id, token, state='FAILED', phase='FAILED', message='任务处理失败，请重试',
                        error_type=type(exc).__name__, error_detail=error, finished_at=time.time(), next_retry_at=None)
                    raise
                delay = 2 ** (index + 1)
                update_attempt(task_id, token, phase='WAITING_RETRY', message='临时网络错误，等待自动重试',
                    next_retry_at=time.time() + delay, attempts=index + 2, error_type=type(exc).__name__, error_detail=error)
                stopped.wait(delay)
                update_attempt(task_id, token, next_retry_at=None)
    finally:
        stopped.set()


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    execute()
