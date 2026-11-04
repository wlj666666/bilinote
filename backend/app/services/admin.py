"""Metadata-only reads for the single-host administration console."""
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import JSON, case, cast, func, or_, select

from app.db import engine as db_module
from app.db.models.chat_jobs import ChatJob
from app.db.models.task_jobs import TaskJob
from app.db.models.users import User
from app.db.models.video_tasks import VideoTask
from app.services.user_data import read_json, task_notes_dir
from app.utils.secrets import redact_secrets

PHASES = {'PENDING', 'PARSING', 'DOWNLOADING', 'DETECTING_SUBTITLES', 'EXTRACTING_SUBTITLES',
          'TRANSCRIBING', 'SUMMARIZING', 'FORMATTING', 'SAVING', 'WAITING_RESOURCE',
          'WAITING_RETRY', 'SUCCESS', 'FAILED', 'CANCELLED', 'UNKNOWN'}
STATES = {'QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED', 'UNKNOWN'}
CHAT_STATES = {'queued', 'running', 'completed', 'failed'}


def object_json(value):
    try:
        parsed = json.loads(value or '{}')
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def timestamp(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()
    return value


def safe_text(value, limit=2000):
    return redact_secrets(str(value or ''))[:limit]


def json_field(column, key):
    # Extract only model identifiers, never the note or question alongside them.
    if db_module.engine.dialect.name == 'sqlite':
        valid = case((func.json_valid(column) == 1, column), else_='{}')
        return func.json_extract(valid, '$.' + key)
    return cast(column, JSON)[key].as_string()


def phase_expression():
    return func.coalesce(TaskJob.phase, VideoTask.legacy_status, 'UNKNOWN')


def state_expression():
    phase = phase_expression()
    return case(
        (TaskJob.task_id.is_not(None), TaskJob.state),
        (phase.in_(['SUCCESS', 'FAILED', 'CANCELLED']), phase),
        (phase == 'PENDING', 'QUEUED'),
        (phase.in_(PHASES - {'PENDING', 'SUCCESS', 'FAILED', 'CANCELLED', 'UNKNOWN'}), 'RUNNING'),
        else_='UNKNOWN',
    )


def note_query():
    return (select(
        VideoTask.task_id.label('id'), VideoTask.user_id, User.username,
        User.enabled.label('user_enabled'), VideoTask.title, VideoTask.platform,
        state_expression().label('state'), phase_expression().label('phase'),
        VideoTask.created_at.label('task_created_at'), TaskJob.created_at,
        TaskJob.started_at, TaskJob.finished_at, TaskJob.updated_at,
        TaskJob.message, TaskJob.error_type, TaskJob.error_detail,
        TaskJob.attempts, TaskJob.recoveries, TaskJob.heartbeat_at, TaskJob.stage_times,
        json_field(VideoTask.form_data, 'model_name').label('model_name'),
        json_field(VideoTask.form_data, 'provider_id').label('provider_id'),
    ).outerjoin(TaskJob, TaskJob.task_id == VideoTask.task_id)
        .outerjoin(User, User.id == VideoTask.user_id).where(VideoTask.deleted.is_(False)))


def note_item(row, now, detail=False):
    created = row.created_at if row.created_at is not None else timestamp(row.task_created_at)
    elapsed = None
    if created is not None:
        end = row.finished_at if row.finished_at is not None else (now if row.state in {'QUEUED', 'RUNNING'} else None)
        if end is not None:
            elapsed = max(0, end - created)
    item = {
        'id': row.id, 'task_id': row.id, 'kind': 'note', 'user_id': row.user_id,
        'username': row.username, 'user_enabled': row.user_enabled,
        'title': safe_text(row.title, 1024) or None, 'platform': row.platform,
        'state': row.state, 'phase': row.phase, 'created_at': created,
        'started_at': row.started_at, 'finished_at': row.finished_at,
        'elapsed_seconds': elapsed, 'updated_at': row.updated_at,
        'model_name': safe_text(row.model_name, 256) or None,
        'provider_id': safe_text(row.provider_id, 256) or None,
        'can_cancel': row.state == 'QUEUED' and row.updated_at is not None,
        'can_retry': row.state == 'FAILED' and row.updated_at is not None and row.user_enabled is True,
    }
    if detail:
        stages = object_json(row.stage_times)
        item.update({
            'message': safe_text(row.message), 'error_type': safe_text(row.error_type, 80) or None,
            'error_detail': safe_text(row.error_detail) or None,
            'attempts': row.attempts, 'recoveries': row.recoveries,
            'heartbeat_at': row.heartbeat_at,
            'stage_times': {key: value for key, value in stages.items()
                            if key in PHASES and isinstance(value, (int, float))
                            and math.isfinite(value) and value >= 0},
        })
    return item


def chat_query():
    return (select(
        ChatJob.id, ChatJob.task_id, ChatJob.user_id, User.username,
        User.enabled.label('user_enabled'), VideoTask.title, VideoTask.platform,
        ChatJob.state, ChatJob.created_at, ChatJob.finished_at, ChatJob.error,
        json_field(ChatJob.payload, 'model_name').label('model_name'),
        json_field(ChatJob.payload, 'provider_id').label('provider_id'),
    ).join(VideoTask, VideoTask.task_id == ChatJob.task_id)
        .outerjoin(User, User.id == ChatJob.user_id).where(VideoTask.deleted.is_(False)))


def chat_item(row, now, detail=False):
    # Older chat jobs have no start timestamp: expose total elapsed, not invented processing time.
    end = row.finished_at if row.finished_at is not None else (now if row.state in {'queued', 'running'} else None)
    item = {
        'id': row.id, 'task_id': row.task_id, 'kind': 'chat', 'user_id': row.user_id,
        'username': row.username, 'user_enabled': row.user_enabled,
        'title': safe_text(row.title, 1024) or None, 'platform': row.platform,
        'state': row.state, 'phase': row.state, 'created_at': row.created_at,
        'started_at': None, 'finished_at': row.finished_at, 'updated_at': None,
        'elapsed_seconds': max(0, end - row.created_at) if end is not None else None,
        'model_name': safe_text(row.model_name, 256) or None,
        'provider_id': safe_text(row.provider_id, 256) or None,
        'can_cancel': False, 'can_retry': False,
    }
    if detail:
        item.update({'message': safe_text(row.error), 'error_detail': safe_text(row.error) or None,
                     'error_type': None, 'attempts': None, 'recoveries': None,
                     'heartbeat_at': None, 'stage_times': {}})
    return item


def task_list(kind='note', user_id=None, state=None, q='', page=1, page_size=20):
    now = time.time()
    if kind == 'note':
        query = note_query()
        if state and state not in STATES | PHASES:
            raise HTTPException(400, '无效的任务状态')
        if state:
            query = query.where(state_expression() == state if state in STATES else phase_expression() == state)
        if q:
            query = query.where(VideoTask.task_id.contains(q, autoescape=True))
        submitted = func.coalesce(TaskJob.created_at, func.extract('epoch', VideoTask.created_at))
        order = (submitted.desc(), VideoTask.id.desc())
        formatter = note_item
    else:
        query = chat_query()
        if state and state not in CHAT_STATES:
            raise HTTPException(400, '无效的问答状态')
        if state:
            query = query.where(ChatJob.state == state)
        if q:
            query = query.where(or_(ChatJob.id.contains(q, autoescape=True), ChatJob.task_id.contains(q, autoescape=True)))
        order = (ChatJob.created_at.desc(), ChatJob.id.desc())
        formatter = chat_item
    if user_id is not None:
        query = query.where((VideoTask.user_id if kind == 'note' else ChatJob.user_id) == user_id)
    with db_module.SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.execute(query.order_by(*order).offset((page - 1) * page_size).limit(page_size)).all()
    return {'items': [formatter(row, now) for row in rows], 'total': total,
            'page': page, 'page_size': page_size, 'observed_at': now}


def task_detail(kind, task_id):
    query = note_query().where(VideoTask.task_id == task_id) if kind == 'note' else chat_query().where(ChatJob.id == task_id)
    with db_module.SessionLocal() as db:
        row = db.execute(query).first()
    if row is None:
        raise HTTPException(404, '任务不存在')
    return (note_item if kind == 'note' else chat_item)(row, time.time(), detail=True)


def overview():
    now = time.time()
    with db_module.SessionLocal() as db:
        # One SQLite read snapshot keeps cards and recent records consistent during task updates.
        if db.bind.dialect.name == 'sqlite':
            db.connection().exec_driver_sql('BEGIN')
        users = db.scalar(select(func.count()).select_from(User))
        states = dict(db.execute(select(state_expression(), func.count()).select_from(VideoTask)
            .outerjoin(TaskJob, TaskJob.task_id == VideoTask.task_id)
            .where(VideoTask.deleted.is_(False)).group_by(state_expression())).all())
        chat_states = dict(db.execute(select(ChatJob.state, func.count()).join(VideoTask, VideoTask.task_id == ChatJob.task_id)
            .where(VideoTask.deleted.is_(False)).group_by(ChatJob.state)).all())
        failed = db.execute(note_query().where(state_expression() == 'FAILED')
                            .order_by(TaskJob.updated_at.desc(), VideoTask.id.desc()).limit(5)).all()
        failed_chats = db.execute(chat_query().where(ChatJob.state == 'failed')
                                  .order_by(ChatJob.created_at.desc(), ChatJob.id.desc()).limit(3)).all()
    return {
        'observed_at': now, 'user_count': users, 'note_total': sum(states.values()),
        'note_states': states, 'chat_states': chat_states,
        'recent_failed_notes': [note_item(row, now) for row in failed],
        'recent_failed_chats': [chat_item(row, now) for row in failed_chats],
    }


def users_page(q='', enabled=None, page=1, page_size=20):
    counts = select(VideoTask.user_id, func.count().label('task_count')).where(
        VideoTask.deleted.is_(False)).group_by(VideoTask.user_id).subquery()
    query = select(User, func.coalesce(counts.c.task_count, 0)).outerjoin(counts, counts.c.user_id == User.id)
    if q:
        query = query.where(User.username.contains(q.lower(), autoescape=True))
    if enabled is not None:
        query = query.where(User.enabled.is_(enabled))
    with db_module.SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.execute(query.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {'items': [{'id': user.id, 'username': user.username, 'role': user.role,
                       'enabled': user.enabled, 'created_at': user.created_at,
                       'last_login_at': user.last_login_at, 'task_count': count} for user, count in rows],
            'total': total, 'page': page, 'page_size': page_size, 'observed_at': time.time()}


def backfill_metadata():
    """One-time, batched legacy import. Polling never scans note files or bodies."""
    cursor = 0
    root = Path(os.getenv('NOTE_OUTPUT_DIR', 'note_results'))
    while True:
        with db_module.SessionLocal() as db:
            tasks = db.scalars(select(VideoTask).outerjoin(TaskJob, TaskJob.task_id == VideoTask.task_id)
                .where(VideoTask.id > cursor, VideoTask.deleted.is_(False),
                       or_(VideoTask.title.is_(None),
                           (TaskJob.task_id.is_(None) & VideoTask.legacy_status.is_(None))))
                .order_by(VideoTask.id).limit(100)).all()
            if not tasks:
                return
            for task in tasks:
                try:
                    directory = task_notes_dir(task, root)
                except HTTPException:
                    # A corrupt old path must not prevent the entire service from starting.
                    task.title = ''
                    task.legacy_status = 'UNKNOWN'
                    continue
                display = object_json(task.display_data)
                audio = read_json(directory / f'{task.task_id}_audio.json')
                result = {}
                if not audio.get('title'):
                    result = read_json(directory / f'{task.task_id}.json')
                meta = result.get('audio_meta') or display.get('audioMeta') or {}
                if not isinstance(meta, dict):
                    meta = {}
                if task.title is None:
                    # Source workers may already be filling a title during startup.
                    # Conditional SQL avoids overwriting a newly recorded value.
                    db.query(VideoTask).filter(VideoTask.id == task.id, VideoTask.title.is_(None)).update(
                        {'title': safe_text(audio.get('title') or meta.get('title'), 1024)}, synchronize_session=False)
                if task.legacy_status is None:
                    status = read_json(directory / f'{task.task_id}.status.json').get('status')
                    if not status and not display.get('status') and not result:
                        result = read_json(directory / f'{task.task_id}.json')
                    status = status or display.get('status') or ('SUCCESS' if result.get('markdown') else 'UNKNOWN')
                    task.legacy_status = status if status in PHASES else 'UNKNOWN'
            cursor = tasks[-1].id
            db.commit()


def record_title(task_id, user_id, title):
    with db_module.SessionLocal.begin() as db:
        db.query(VideoTask).filter_by(task_id=task_id, user_id=user_id, deleted=False).update(
            {'title': safe_text(title, 1024)}, synchronize_session=False)
