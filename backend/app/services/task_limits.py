"""One source of configuration for API admission, workers and resource gates."""
import os
from dataclasses import dataclass
from pathlib import Path


def positive_env(name, default):
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f'{name} 必须大于 0')
    return value


@dataclass(frozen=True)
class TaskLimits:
    active: int = 6
    user_active: int = 1
    user_queued: int = 3
    queued: int = 20
    cpu: int = 2
    gpu: int = 1
    api: int = 2

    @classmethod
    def from_env(cls):
        limits = cls(**{key: positive_env(name, default) for key, name, default in (
            ('active', 'TASK_MAX_WORKERS', 6), ('user_active', 'TASK_USER_ACTIVE', 1),
            ('user_queued', 'TASK_USER_QUEUE_LIMIT', 3), ('queued', 'TASK_QUEUE_LIMIT', 20),
            ('cpu', 'TASK_CPU_SLOTS', 2), ('gpu', 'TASK_GPU_SLOTS', 1), ('api', 'TASK_API_SLOTS', 2))})
        if limits.gpu != 1:
            raise ValueError('当前单 GPU 推理服务要求 TASK_GPU_SLOTS=1')
        return limits


def runtime_dir():
    path = Path(os.getenv('TASK_RUNTIME_DIR', 'data/task_runtime')).resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path
