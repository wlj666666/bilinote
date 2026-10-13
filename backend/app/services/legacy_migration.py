"""Idempotently assign pre-login data to the original administrator."""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db import engine as db_module
from app.db.models.users import User
from app.db.models.video_tasks import VideoTask
from app.db.models.user_data import DataMigration, UserFile, DeletedTask
from app.services.storage_scope import static_root, uploads_root
from app.services.user_data import read_json
from app.utils.request_paths import validate_task_id

MIGRATION_NAME = 'legacy-user-ownership-v1'


def backup_database(engine) -> str | None:
    if engine.url.get_backend_name() != 'sqlite' or engine.url.database in {None, '', ':memory:'}:
        return None
    source = Path(engine.url.database).resolve()
    if not source.is_file():
        return None
    directory = source.parent / 'backups' / 'user-isolation'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}.db'
    with sqlite3.connect(str(source)) as original, sqlite3.connect(str(target)) as copy:
        original.backup(copy)
    target.chmod(0o600)
    return str(target)


def migrate_legacy_data(notes_root: str | Path | None = None, admin_username: str | None = None) -> dict:
    notes = Path(notes_root or os.getenv('NOTE_OUTPUT_DIR', 'note_results')).resolve()
    with db_module.SessionLocal() as db:
        marker = db.get(DataMigration, MIGRATION_NAME)
        previous = json.loads(marker.value) if marker else {}
        db.autoflush = False
        query = select(User).where(User.role == 'admin').order_by(User.id)
        if admin_username:
            query = query.where(User.username == admin_username)
        admin = db.get(User, previous['admin_id']) if marker else db.scalar(query)
        if admin is None:
            return {'pending_admin': True}
        tasks = {task.task_id: task for task in db.scalars(select(VideoTask)).all()}
        deleted_ids = set(db.scalars(select(DeletedTask.task_id)).all())
        assigned = 0
        for task in tasks.values():
            if task.user_id is None:
                task.user_id = admin.id
                assigned += 1
        candidates = {p.name.removesuffix('.status.json') for p in notes.glob('*.status.json') if not p.name.endswith('_markdown.status.json')}
        for path in notes.glob('*.json'):
            if path.name.endswith('.status.json'):
                continue
            result = read_json(path)
            if 'markdown' in result and 'audio_meta' in result:
                candidates.add(path.stem)
        for task_id in sorted(candidates):
            try:
                validate_task_id(task_id)
            except Exception:
                continue
            if task_id in tasks or task_id in deleted_ids:
                continue
            result = read_json(notes / f'{task_id}.json')
            audio = result.get('audio_meta') or read_json(notes / f'{task_id}_audio.json')
            if not isinstance(audio, dict):
                audio = {}
            status_file = notes / f'{task_id}.status.json'
            timestamp_file = notes / f'{task_id}.json'
            if not timestamp_file.exists():
                timestamp_file = status_file
            task = VideoTask(task_id=task_id, user_id=admin.id, storage_relpath='', video_id=str(audio.get('video_id') or ''),
                             platform=audio.get('platform') or 'bilibili',
                             created_at=datetime.fromtimestamp(timestamp_file.stat().st_mtime, timezone.utc))
            db.add(task)
            tasks[task_id] = task
            assigned += 1
        registered = 0
        for kind, root in [('static', static_root()), ('uploads', uploads_root())]:
            existing = {file.path for file in db.scalars(select(UserFile).where(UserFile.kind == kind)).all()}
            for path in root.rglob('*'):
                if not path.is_file() or path.is_symlink() or path.relative_to(root).parts[0] == 'users':
                    continue
                relative = path.relative_to(root).as_posix()
                if relative not in existing:
                    db.add(UserFile(user_id=admin.id, kind=kind, path=relative))
                    registered += 1
        if marker and assigned == 0 and registered == 0:
            return {'already_migrated': True, **previous}
        backup = backup_database(db_module.engine)
        summary = {'admin_id': admin.id, 'admin_username': admin.username,
                   'tasks_assigned': previous.get('tasks_assigned', 0) + assigned,
                   'files_registered': previous.get('files_registered', 0) + registered, 'backup': backup}
        if marker:
            marker.value = json.dumps(summary, ensure_ascii=False)
        else:
            db.add(DataMigration(name=MIGRATION_NAME, value=json.dumps(summary, ensure_ascii=False)))
        db.commit()
        return summary
