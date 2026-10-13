"""Serialize task deletion against indexing and late chat writes in this worker."""
from contextlib import contextmanager
from threading import Lock, RLock
from weakref import WeakValueDictionary

_guard = Lock()
_locks = WeakValueDictionary()


@contextmanager
def task_data_lock(user_id: int, task_id: str):
    key = (user_id, task_id)
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = RLock()
            _locks[key] = lock
    with lock:
        yield
