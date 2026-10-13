import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from app.db import engine as db_module
from app.db.models.video_tasks import VideoTask
from app.db.models.user_data import ChatEntry, UserFile
from app.utils.request_paths import validate_task_id
from app.utils.secrets import customer_result


def require_task(task_id: str, user_id: int) -> VideoTask:
    validate_task_id(task_id)
    with db_module.SessionLocal() as db:
        task = db.scalar(select(VideoTask).where(VideoTask.task_id == task_id, VideoTask.user_id == user_id, VideoTask.deleted.is_(False)))
        if task is None:
            raise HTTPException(404, '任务不存在')
        db.expunge(task)
        return task


def task_notes_dir(task: VideoTask, root: str | Path) -> Path:
    root = Path(root).resolve()
    path = (root / task.storage_relpath).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(404, '任务不存在')
    return path


def create_task(task_id: str, user_id: int, form_data: dict) -> VideoTask:
    task = VideoTask(task_id=task_id, user_id=user_id, video_id='', platform=form_data['platform'],
                     storage_relpath=f'users/{user_id}', form_data=json.dumps(form_data, ensure_ascii=False))
    with db_module.SessionLocal() as db:
        db.add(task)
        db.commit()
        db.refresh(task)
        db.expunge(task)
    return task


def save_form(task_id: str, user_id: int, form_data: dict):
    with db_module.SessionLocal() as db:
        task = db.scalar(select(VideoTask).where(VideoTask.task_id == task_id, VideoTask.user_id == user_id, VideoTask.deleted.is_(False)))
        if task is None:
            raise HTTPException(404, '任务不存在')
        task.form_data = json.dumps(form_data, ensure_ascii=False)
        db.commit()


def register_file(kind: str, path: Path, user_id: int, task_id: str | None, root: Path) -> str:
    root, path = root.resolve(), path.resolve()
    if not path.is_relative_to(root):
        raise ValueError('文件超出存储目录')
    relative = path.relative_to(root).as_posix()
    with db_module.SessionLocal() as db:
        existing = db.scalar(select(UserFile).where(UserFile.kind == kind, UserFile.path == relative))
        if existing and existing.user_id != user_id:
            raise HTTPException(404, '文件不存在')
        if not existing:
            db.add(UserFile(kind=kind, path=relative, user_id=user_id, task_id=task_id))
            db.commit()
    return relative


def require_file(kind: str, relative: str, user_id: int, root: Path) -> Path:
    path = (root.resolve() / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise HTTPException(404, '文件不存在')
    relative = path.relative_to(root.resolve()).as_posix()
    with db_module.SessionLocal() as db:
        file = db.scalar(select(UserFile).where(UserFile.kind == kind, UserFile.path == relative, UserFile.user_id == user_id))
        if file is None:
            raise HTTPException(404, '文件不存在')
        task_id = file.task_id
    if task_id:
        require_task(task_id, user_id)
    if not path.is_file():
        raise HTTPException(404, '文件不存在')
    return path


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def display_task(task: VideoTask, root: str | Path, admin: bool = False, default_model: dict | None = None) -> dict:
    directory = task_notes_dir(task, root)
    result = read_json(directory / f'{task.task_id}.json')
    status = read_json(directory / f'{task.task_id}.status.json')
    display = json.loads(task.display_data or '{}')
    form = json.loads(task.form_data or '{}') or display.get('formData') or {}
    audio = result.get('audio_meta') or display.get('audioMeta') or {}
    raw = audio.get('raw_info') or {}
    video_url = raw.get('webpage_url') or raw.get('original_url') or ''
    video_id = audio.get('video_id') or task.video_id
    if not video_url and video_id:
        templates = {'bilibili': 'https://www.bilibili.com/video/{}', 'youtube': 'https://www.youtube.com/watch?v={}',
                     'douyin': 'https://www.douyin.com/video/{}', 'kuaishou': 'https://www.kuaishou.com/short-video/{}'}
        if task.platform in templates:
            video_url = templates[task.platform].format(video_id)
    if task.platform == 'local':
        video_url = audio.get('video_path') or raw.get('path') or video_url
        from app.services.storage_scope import uploads_root
        from urllib.parse import quote
        source = Path(video_url).resolve() if video_url else None
        if source and source.is_relative_to(uploads_root()):
            video_url = '/uploads/' + quote(source.relative_to(uploads_root()).as_posix(), safe='/')
    if default_model and not form.get('model_name'):
        form = {**form, 'model_name': default_model['model_name'], 'provider_id': default_model['provider_id']}
    created = task.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    result_status = status.get('status') or ('SUCCESS' if result.get('markdown') else display.get('status', 'FAILED'))
    markdown = display.get('markdown') or result.get('markdown') or ''
    # Keep imported browser versions; a new generation is added when it differs.
    latest = result.get('markdown')
    if latest and isinstance(markdown, list) and not any(v.get('content') == latest for v in markdown):
        markdown = [{'ver_id': f'{task.task_id}-{uuid.uuid5(uuid.NAMESPACE_URL, latest)}', 'content': latest,
                     'style': form.get('style', ''), 'model_name': form.get('model_name', ''),
                     'created_at': datetime.now(timezone.utc).isoformat()}, *markdown]
    elif latest and not isinstance(markdown, list):
        markdown = latest
    message = status.get('message') or display.get('message') or ''
    if result_status == 'FAILED' and not admin:
        message = '任务处理失败，请重试或联系管理员'
    response = {'id': task.task_id, 'markdown': markdown, 'transcript': result.get('transcript') or display.get('transcript') or {'full_text': '', 'language': '', 'segments': [], 'raw': {}},
                'status': result_status, 'message': message, 'audioMeta': audio,
                'createdAt': display.get('createdAt') or (created.isoformat() if created else ''),
                'platform': task.platform, 'formData': {'video_url': video_url,
                    'platform': task.platform, 'quality': 'medium', 'model_name': '', 'provider_id': '', 'style': 'minimal', **form}}
    return response if admin else customer_result(response)


def chat_history(task_id: str, user_id: int) -> list[dict]:
    require_task(task_id, user_id)
    with db_module.SessionLocal() as db:
        return [json.loads(row.payload) for row in db.scalars(select(ChatEntry).where(ChatEntry.user_id == user_id, ChatEntry.task_id == task_id).order_by(ChatEntry.id)).all()]


def append_chat(task_id: str, user_id: int, messages: list[dict]):
    from app.services.task_data_lock import task_data_lock
    with task_data_lock(user_id, task_id):
        require_task(task_id, user_id)
        with db_module.SessionLocal() as db:
            db.add_all([ChatEntry(task_id=task_id, user_id=user_id, payload=json.dumps(message, ensure_ascii=False)) for message in messages])
            db.commit()


def save_generated_versions(task_id: str, user_id: int, markdown: str, previous: str | None):
    require_task(task_id, user_id)
    with db_module.SessionLocal() as db:
        task = db.scalar(select(VideoTask).where(VideoTask.task_id == task_id, VideoTask.user_id == user_id, VideoTask.deleted.is_(False)))
        if task is None:
            raise HTTPException(404, '任务不存在')
        display = json.loads(task.display_data or '{}')
        form = json.loads(task.form_data or '{}')
        versions = display.get('markdown') or previous or []
        def version(content):
            return {'ver_id': f'{task_id}-{uuid.uuid5(uuid.NAMESPACE_URL, content)}', 'content': content,
                    'style': form.get('style', ''), 'model_name': form.get('model_name', ''),
                    'created_at': datetime.now(timezone.utc).isoformat()}
        if isinstance(versions, str):
            versions = [version(versions)]
        if not any(item.get('content') == markdown for item in versions):
            versions = [version(markdown), *versions]
        display['markdown'] = versions
        task.display_data = json.dumps(display, ensure_ascii=False)
        db.commit()
