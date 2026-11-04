"""Atomic admission and fair scheduling. SQLite writes use BEGIN IMMEDIATE."""
import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from fastapi import HTTPException
from sqlalchemy import select, func
from app.db import engine as db_module
from app.db.models.task_jobs import TaskJob, TaskSubmission, TaskSchedulerState
from app.db.models.video_tasks import VideoTask
from app.db.models.users import User
from app.services.task_limits import TaskLimits

TERMINAL = {'SUCCESS', 'FAILED', 'CANCELLED'}


@contextmanager
def transaction():
    with db_module.SessionLocal() as db:
        try:
            if db.bind.dialect.name == 'sqlite':
                db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            else:
                db.execute(select(TaskSchedulerState).where(TaskSchedulerState.id == 1).with_for_update())
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


def enqueue(user_id, payload, task_id=None, request_id=None, limits=None,
            expected_state=None, expected_updated_at=None):
    limits = limits or TaskLimits.from_env()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(json.dumps(payload['form'], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with transaction() as db:
        user = db.get(User, user_id)
        if user is None or not user.enabled:
            raise HTTPException(403, '账号不可用')
        if request_id:
            previous = db.scalar(select(TaskSubmission).where(
                TaskSubmission.user_id == user_id, TaskSubmission.request_id == request_id))
            if previous:
                if previous.fingerprint != fingerprint or (task_id and task_id != previous.task_id):
                    raise HTTPException(409, '同一个提交编号不能用于不同任务参数')
                task = db.scalar(select(VideoTask).where(VideoTask.task_id == previous.task_id,
                                 VideoTask.user_id == user_id, VideoTask.deleted.is_(False)))
                if task is None:
                    raise HTTPException(410, '该提交对应的任务已删除')
                return previous.task_id
        job = db.get(TaskJob, task_id) if task_id else None
        task = db.scalar(select(VideoTask).where(VideoTask.task_id == task_id,
                         VideoTask.user_id == user_id, VideoTask.deleted.is_(False))) if task_id else None
        if task_id and task is None:
            raise HTTPException(404, '任务不存在')
        if expected_state is not None and (
                job is None or job.state != expected_state or
                (expected_updated_at is not None and job.updated_at != expected_updated_at)):
            raise HTTPException(409, '任务状态已变化，请刷新后重试')
        if job and job.state not in TERMINAL:
            if json.loads(job.payload)['form'] != payload['form']:
                raise HTTPException(409, '任务正在排队或执行，不能修改参数；请结束后再重试')
            if request_id:
                db.add(TaskSubmission(user_id=user_id, request_id=request_id, task_id=task_id, fingerprint=fingerprint))
            return task_id
        queued = db.scalar(select(func.count()).select_from(TaskJob).where(TaskJob.state == 'QUEUED'))
        mine = db.scalar(select(func.count()).select_from(TaskJob).where(
            TaskJob.state == 'QUEUED', TaskJob.user_id == user_id))
        if mine >= limits.user_queued:
            raise HTTPException(429, f'你已有 {limits.user_queued} 个任务等待处理，请完成或取消后再提交', headers={'Retry-After': '10'})
        if queued >= limits.queued:
            raise HTTPException(429, '当前全站排队已满，请稍后提交', headers={'Retry-After': '10'})
        if job is not None:
            # Explicit regeneration of a completed note must produce a fresh
            # summary; retries of failed attempts reuse their signed checkpoint.
            revision = json.loads(job.payload).get('summary_revision')
            if job.state == 'SUCCESS':
                revision = uuid.uuid4().hex
            payload = {**payload, 'summary_revision': revision}
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        form = {k: v for k, v in payload['form'].items() if k != 'prefetched_transcript'}
        if task is None:
            task_id = str(uuid.uuid4())
            task = VideoTask(task_id=task_id, user_id=user_id, video_id='', platform=form['platform'],
                             storage_relpath=f'users/{user_id}')
            db.add(task)
        task.form_data = json.dumps(form, ensure_ascii=False)
        db.flush()  # Persist the owned task before inserting its execution FK.
        if job is None:
            job = TaskJob(task_id=task_id, user_id=user_id)
            db.add(job)
        job.payload = encoded
        job.state, job.phase, job.message = 'QUEUED', 'PENDING', '已提交，等待调度'
        job.attempt_token = None
        job.attempts = job.recoveries = 0
        job.worker_pid = job.heartbeat_at = job.started_at = job.finished_at = job.next_retry_at = None
        job.error_type = job.error_detail = None
        job.stage_times = '{}'
        job.created_at = job.updated_at = time.time()
        if request_id:
            db.add(TaskSubmission(user_id=user_id, request_id=request_id, task_id=task_id, fingerprint=fingerprint))
    return task_id


def claim(limits=None):
    limits = limits or TaskLimits.from_env()
    with transaction() as db:
        active = db.scalars(select(TaskJob).where(TaskJob.state == 'RUNNING')).all()
        # Recovered workflows retain their execution reservation, so a full queue
        # does not grow beyond its admission limit after a restart.
        for recovered in active:
            if recovered.attempt_token is None:
                recovered.attempt_token = uuid.uuid4().hex
                recovered.attempts += 1
                recovered.started_at = recovered.heartbeat_at = recovered.updated_at = time.time()
                return recovered.task_id, recovered.attempt_token
        if len(active) >= limits.active:
            return None
        counts = {}
        for job in active:
            counts[job.user_id] = counts.get(job.user_id, 0) + 1
        queued = db.scalars(select(TaskJob).join(VideoTask, VideoTask.task_id == TaskJob.task_id)
            .join(User, User.id == TaskJob.user_id).where(TaskJob.state == 'QUEUED',
                VideoTask.deleted.is_(False), User.enabled.is_(True))
            .order_by(TaskJob.created_at, TaskJob.task_id)).all()
        candidates = {}
        for job in queued:
            if counts.get(job.user_id, 0) < limits.user_active:
                candidates.setdefault(job.user_id, job)
        if not candidates:
            return None
        state = db.get(TaskSchedulerState, 1)
        users = sorted(candidates)
        after = [user_id for user_id in users if state.last_user_id is not None and user_id > state.last_user_id]
        user_id = (after or users)[0]
        job = candidates[user_id]
        job.state, job.phase, job.message = 'RUNNING', 'PARSING', '正在准备任务'
        job.attempt_token = uuid.uuid4().hex
        job.attempts += 1
        job.started_at = job.heartbeat_at = job.updated_at = time.time()
        state.last_user_id = user_id
        return job.task_id, job.attempt_token


def cancel(task_id, user_id):
    with transaction() as db:
        job = db.get(TaskJob, task_id)
        if job is None or job.user_id != user_id:
            raise HTTPException(404, '任务不存在')
        if job.state == 'CANCELLED':
            return
        if job.state != 'QUEUED':
            raise HTTPException(409, '任务已开始执行或已结束，不能取消')
        job.state, job.phase, job.message = 'CANCELLED', 'CANCELLED', '任务已取消'
        job.finished_at = job.updated_at = time.time()


def update_attempt(task_id, token, **fields):
    """Fence all writes from a previous or completed execution attempt."""
    if 'phase' in fields:
        with transaction() as db:
            job = db.get(TaskJob, task_id)
            if job is None or job.state != 'RUNNING' or job.attempt_token != token:
                return False
            now = time.time()
            timing = json.loads(job.stage_times)
            if job.phase != fields['phase'] or fields.get('state') in TERMINAL:
                since = timing.pop('_phase_started_at', job.started_at or now)
                timing[job.phase] = timing.get(job.phase, 0) + max(0, now - since)
                if fields.get('state') not in TERMINAL:
                    timing['_phase_started_at'] = now
            job.stage_times = json.dumps(timing)
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = now
            return True
    with db_module.SessionLocal.begin() as db:
        count = db.query(TaskJob).filter_by(task_id=task_id, state='RUNNING', attempt_token=token).update(
            {**fields, 'updated_at': time.time()}, synchronize_session=False)
        return bool(count)


def public_job(task_id, admin=False):
    with db_module.SessionLocal() as db:
        job = db.get(TaskJob, task_id)
        if job is None:
            return None
        message = job.message
        if job.state == 'FAILED' and not admin:
            message = '任务处理失败，请重试或联系管理员'
        return {'status': job.phase, 'message': message, 'task_id': task_id,
                'can_cancel': job.state == 'QUEUED', 'attempts': job.attempts}


def recover_jobs():
    """Called only after obtaining the singleton lock (old workers have exited)."""
    with transaction() as db:
        for job in db.scalars(select(TaskJob).where(TaskJob.state == 'RUNNING')).all():
            job.recoveries += 1
            job.worker_pid = None
            job.attempt_token = None
            job.heartbeat_at = None
            job.updated_at = time.time()
            if job.recoveries > 3:
                job.state, job.phase, job.message = 'FAILED', 'FAILED', '任务多次中断，请检查运行环境后重试'
                job.finished_at = time.time()
            else:
                job.state, job.phase, job.message = 'RUNNING', 'WAITING_RETRY', '服务重启，正在恢复任务；将复用已完成的缓存'
