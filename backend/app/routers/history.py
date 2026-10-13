import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import engine as db_module
from app.db.models.users import User
from app.db.models.video_tasks import VideoTask
from app.db.models.user_data import ChatEntry, DataMigration, DeletedTask
from app.services.auth import get_current_user, require_admin
from app.services.legacy_migration import MIGRATION_NAME
from app.services.user_data import display_task, require_task, task_notes_dir
from app.utils.response import ResponseWrapper as R
from app.utils.request_paths import validate_task_id
from fastapi.responses import Response

router = APIRouter()


def note_root():
    from app.routers.note import NOTE_OUTPUT_DIR
    return Path(NOTE_OUTPUT_DIR)


@router.get('/tasks')
def list_tasks(user: User = Depends(get_current_user), limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    from app.services.available_models import available_models
    models = available_models()
    with db_module.SessionLocal() as db:
        tasks = db.scalars(select(VideoTask).where(VideoTask.user_id == user.id, VideoTask.deleted.is_(False))
                           .order_by(VideoTask.created_at.desc(), VideoTask.id.desc()).offset(offset).limit(limit + 1)).all()
        return R.success({'tasks': [display_task(task, note_root(), user.role == 'admin', models[0] if models else None) for task in tasks[:limit]], 'has_more': len(tasks) > limit})


@router.get('/tasks/{task_id}/export')
def export_task(task_id: str, user: User = Depends(get_current_user)):
    task = require_task(task_id, user.id)
    display = display_task(task, note_root(), user.role == 'admin')
    markdown = display['markdown']
    if isinstance(markdown, list):
        markdown = markdown[0].get('content', '') if markdown else ''
    return Response(markdown, media_type='text/markdown', headers={'Content-Disposition': f'attachment; filename="{task_id}.md"', 'Cache-Control': 'no-store'})


class DisplayUpdate(BaseModel):
    markdown: str | list[dict] | None = None


@router.patch('/tasks/{task_id}/display')
def update_display(task_id: str, data: DisplayUpdate, user: User = Depends(get_current_user)):
    require_task(task_id, user.id)
    with db_module.SessionLocal() as db:
        task = db.scalar(select(VideoTask).where(VideoTask.task_id == task_id, VideoTask.user_id == user.id, VideoTask.deleted.is_(False)))
        if task is None:
            raise HTTPException(404, '任务不存在')
        display = json.loads(task.display_data or '{}')
        if data.markdown is not None:
            display['markdown'] = data.markdown
        task.display_data = json.dumps(display, ensure_ascii=False)
        db.commit()
    return R.success()


class LegacyImport(BaseModel):
    tasks: list[dict] = Field(default_factory=list, max_length=200)
    chats: dict[str, list[dict]] = Field(default_factory=dict)


@router.post('/history/import')
def import_legacy_history(data: LegacyImport, admin: User = Depends(require_admin)):
    """Only the designated legacy owner may import old, unscoped browser caches."""
    with db_module.SessionLocal() as db:
        marker = db.get(DataMigration, MIGRATION_NAME)
        if marker is None or json.loads(marker.value)['admin_id'] != admin.id:
            raise HTTPException(403, '旧历史仅可归入原管理员账号')
        imported = 0
        for item in data.tasks:
            task_id = validate_task_id(str(item.get('id', '')))
            if db.get(DeletedTask, task_id) is not None:
                continue
            task = db.scalar(select(VideoTask).where(VideoTask.task_id == task_id))
            if task and task.user_id != admin.id:
                raise HTTPException(404, '任务不存在')
            if task and task.deleted:
                continue
            if task is None:
                form = item.get('formData') or {}
                task = VideoTask(task_id=task_id, user_id=admin.id, storage_relpath='', video_id=str((item.get('audioMeta') or {}).get('video_id') or ''),
                                 platform=item.get('platform') or 'bilibili', form_data=json.dumps(form, ensure_ascii=False))
                db.add(task)
            # First import wins: retries cannot overwrite newer server-side edits.
            if task.display_data is None:
                allowed = {key: item[key] for key in ('markdown', 'transcript', 'audioMeta', 'createdAt', 'formData', 'status', 'message') if key in item}
                task.display_data = json.dumps(allowed, ensure_ascii=False)
                if task.form_data is None and item.get('formData'):
                    task.form_data = json.dumps(item['formData'], ensure_ascii=False)
                imported += 1
        db.flush()
        for task_id, messages in data.chats.items():
            validate_task_id(task_id)
            task = db.scalar(select(VideoTask).where(VideoTask.task_id == task_id, VideoTask.user_id == admin.id, VideoTask.deleted.is_(False)))
            if task is None:
                continue
            existing = db.scalar(select(ChatEntry.id).where(ChatEntry.user_id == admin.id, ChatEntry.task_id == task_id).limit(1))
            if existing is None:
                for message in messages:
                    if message.get('role') in {'user', 'assistant'} and isinstance(message.get('content'), str):
                        db.add(ChatEntry(task_id=task_id, user_id=admin.id, payload=json.dumps(message, ensure_ascii=False)))
        db.commit()
    return R.success({'imported': imported})
