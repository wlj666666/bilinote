"""Task-local storage context; never mutate environment variables for a request."""
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StorageScope:
    user_id: int
    task_id: str
    notes_dir: Path


current_scope: ContextVar[StorageScope | None] = ContextVar('bilinote_storage_scope', default=None)


@contextmanager
def task_scope(user_id: int, task_id: str, notes_dir: Path):
    token = current_scope.set(StorageScope(user_id, task_id, Path(notes_dir)))
    try:
        yield
    finally:
        current_scope.reset(token)


def scoped_notes_dir(fallback: str | Path) -> Path:
    scope = current_scope.get()
    return scope.notes_dir if scope else Path(fallback)


def scoped_runtime_dir(base: str | Path) -> Path:
    scope = current_scope.get()
    path = Path(base)
    if scope:
        path = path / 'users' / str(scope.user_id) / scope.task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def static_root() -> Path:
    return Path(os.getenv('STATIC_ROOT', 'static')).resolve()


def uploads_root() -> Path:
    return Path(os.getenv('UPLOAD_ROOT', 'uploads')).resolve()
