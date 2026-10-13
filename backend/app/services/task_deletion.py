"""Purge one owned task, preserving shared sources and a minimal tombstone."""
import shutil
import sys
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from app.db import engine as db_module
from app.db.models.user_data import ChatEntry, DeletedTask, UserFile
from app.db.models.video_tasks import VideoTask
from app.services.storage_scope import static_root, uploads_root
from app.services.task_data_lock import task_data_lock
from app.services.user_data import task_notes_dir
from app.utils.request_paths import validate_task_id

RUNTIME_ROOT = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
                else Path(__file__).resolve().parents[2] / 'data')


def _bounded_path(root: Path, path: Path) -> Path:
    """Never follow a symlink into another task or outside the storage root."""
    root = root.resolve()
    path = path.absolute()
    if (not path.is_relative_to(root) or path == root
            or not path.parent.resolve().is_relative_to(root)):
        raise ValueError('删除路径超出任务存储范围')
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('任务存储路径包含符号链接')
    return path


def _remove(root: Path, path: Path, summary: dict):
    path = _bounded_path(root, path)
    if path.is_symlink():
        path.unlink()  # Remove the link itself, never its target.
        summary['files_removed'] += 1
    elif path.is_dir():
        files = [file for file in path.rglob('*') if file.is_file() and not file.is_symlink()]
        size = sum(file.stat().st_size for file in files)
        shutil.rmtree(path)
        summary['files_removed'] += len(files)
        summary['bytes_removed'] += size
    elif path.exists():
        size = path.stat().st_size
        path.unlink()
        summary['files_removed'] += 1
        summary['bytes_removed'] += size


def purge_task(task_id: str, user_id: int, notes_root: str | Path,
               upload_root: str | Path | None = None, runtime_root: str | Path | None = None) -> dict:
    validate_task_id(task_id)
    summary = {'tasks_removed': 0, 'files_removed': 0, 'bytes_removed': 0}
    with task_data_lock(user_id, task_id):
        with db_module.SessionLocal() as db:
            task = db.scalar(select(VideoTask).where(VideoTask.task_id == task_id, VideoTask.user_id == user_id))
            tombstone = db.get(DeletedTask, task_id)
            if task is None:
                if tombstone is not None and tombstone.user_id == user_id:
                    return summary
                raise HTTPException(404, '任务不存在')
            if tombstone is not None and tombstone.user_id != user_id:
                raise HTTPException(404, '任务不存在')
            directory = task_notes_dir(task, notes_root)
            # Revoke access first. Keep the task and file manifest until cleanup
            # succeeds so any failed purge can be retried without losing paths.
            task.deleted = True
            if tombstone is None:
                db.add(DeletedTask(task_id=task_id, user_id=user_id))
            files = [(file.kind, file.path) for file in db.scalars(select(UserFile).where(
                UserFile.task_id == task_id, UserFile.user_id == user_id)).all()]
            db.commit()

        # Indexing uses the same lock; it cannot recreate a purged collection.
        from app.services.vector_store import VectorStoreManager
        VectorStoreManager(user_id=user_id).delete_index(task_id, allow_deleted=True)

        # Includes old GPT checkpoints/status and interrupted temporary writes.
        root = Path(notes_root).resolve()
        for path in directory.glob(f'{task_id}*'):
            if path.name.startswith((f'{task_id}.', f'{task_id}_')):
                _remove(root, path, summary)

        storage_roots = {'static': static_root(), 'uploads': Path(upload_root or uploads_root()).resolve()}
        for kind, relative in files:
            if kind not in storage_roots:
                raise ValueError('未知的任务文件类型')
            file_root = storage_roots[kind]
            _remove(file_root, file_root / relative, summary)
        image_root = storage_roots['static']
        _remove(image_root, image_root / 'users' / str(user_id) / task_id, summary)

        runtime = Path(runtime_root or RUNTIME_ROOT).resolve()
        for name in ('media', 'data', 'output_frames', 'grid_output'):
            base = runtime / name
            _remove(runtime, base / 'users' / str(user_id) / task_id, summary)
        # Pre-isolation frame/grid directories were media/<input hash>/<task>.
        # Their parent contains shared audio/video and must stay intact.
        for path in (runtime / 'media').glob(f'*/{task_id}'):
            parent_name = path.parent.name
            if len(parent_name) == 24 and all(char in '0123456789abcdef' for char in parent_name):
                _remove(runtime, path, summary)

        with db_module.SessionLocal() as db:
            db.query(ChatEntry).filter_by(task_id=task_id, user_id=user_id).delete()
            db.query(UserFile).filter_by(task_id=task_id, user_id=user_id).delete()
            summary['tasks_removed'] = db.query(VideoTask).filter_by(task_id=task_id, user_id=user_id).delete()
            db.commit()
    return summary
