"""Prepare one CPU embedding runtime per process using persistent local weights.

Chroma's MiniLM archive comes directly from S3, independently of HF_ENDPOINT.
Compose disables automatic downloads and validates/warms the model at startup.
Explicit provisioning is available through scripts/prepare_embeddings.py; its
file lock prevents concurrent processes from writing the same model archive.
"""
import os
import time
import threading
import tempfile
from functools import cached_property
from pathlib import Path

from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

from app.services.task_limits import positive_env
from app.services.task_process import FileLock
from app.utils.logger import get_logger

logger = get_logger(__name__)

MODEL_NAME = 'all-MiniLM-L6-v2'
# 与 chromadb 内部 _download_model_if_not_exists 校验的文件列表保持一致
_EXTRACTED_FILES = (
    'config.json',
    'model.onnx',
    'special_tokens_map.json',
    'tokenizer.json',
    'tokenizer_config.json',
    'vocab.txt',
)


def _is_complete(directory: Path) -> bool:
    return all((directory / name).is_file() and (directory / name).stat().st_size > 0
               for name in _EXTRACTED_FILES)


def _candidate_model_dirs() -> list[Path]:
    """按优先级列出可能存放预置模型的目录（都指向包含 onnx/ 的模型目录）。"""
    candidates: list[Path] = []
    configured = os.getenv('EMBEDDING_MODEL_DIR')
    if configured:
        # An explicit deployment path must never silently fall back to a container cache.
        return [Path(configured)]
    # 容器内：./backend/models 挂载到 /app/models
    candidates.append(Path('/app/models/chroma/onnx_models') / MODEL_NAME)
    # 源码运行（backend/app/services/ → backend/models）与容器内代码布局（/app/…）都覆盖
    here = Path(__file__).resolve()
    for parent in here.parents[:3]:
        candidates.append(parent / 'models' / 'chroma' / 'onnx_models' / MODEL_NAME)
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory):
            pass
        return True
    except OSError:
        return False


def resolve_download_path() -> Path:
    """选定 ONNX 模型的存放目录，优先命中预置副本以避免联网下载。"""
    candidates = _candidate_model_dirs()
    for candidate in candidates:
        if _is_complete(candidate / ONNXMiniLM_L6_V2.EXTRACTED_FOLDER_NAME):
            return candidate
    # 没有预置副本时落到可写的持久化目录（重建容器不丢），否则退回默认缓存。
    for candidate in candidates:
        if _writable(candidate):
            return candidate
    raise RuntimeError('Embedding 模型目录不可写，请检查 EMBEDDING_MODEL_DIR 和模型卷权限')


class CPUEmbeddingFunction(ONNXMiniLM_L6_V2):
    def __init__(self):
        super().__init__(preferred_providers=['CPUExecutionProvider'])
        self.DOWNLOAD_PATH = resolve_download_path()

    @staticmethod
    def name():
        # Same MiniLM weights/dimensions as Chroma's historical default. Only the
        # runtime provider/thread budget changes; existing indexes stay usable.
        return 'default'

    def get_config(self):
        return {}

    def _download_model_if_not_exists(self) -> None:
        """命中预置模型就直接返回；否则串行下载，避免并发互相踩踏。"""
        target = Path(self.DOWNLOAD_PATH)
        extracted = target / self.EXTRACTED_FOLDER_NAME
        if _is_complete(extracted):
            return

        if os.getenv('EMBEDDING_ALLOW_DOWNLOAD', '1') != '1':
            raise RuntimeError(
                f'Embedding 模型未准备好: {extracted}。请先运行 '
                'python scripts/prepare_embeddings.py，再启动后端；用户请求不会自动下载模型。')

        target.mkdir(parents=True, exist_ok=True)
        lock = FileLock(target / '.download.lock')
        try:
            deadline = time.monotonic() + positive_env('EMBEDDING_DOWNLOAD_LOCK_TIMEOUT', 600)
            while not lock.acquire():
                if time.monotonic() >= deadline:
                    raise TimeoutError('等待 Embedding 模型准备超时，请检查模型准备进程')
                time.sleep(0.2)
            if _is_complete(extracted):
                return
            logger.warning(
                'Embedding 模型缺失，开始下载约 80MB（走 AWS S3，可能较慢）：%s。'
                '可把模型预置到 backend/models/chroma/onnx_models/%s/ 免下载。',
                target, MODEL_NAME,
            )
            super()._download_model_if_not_exists()
            logger.info('Embedding 模型就绪: %s', extracted)
        finally:
            lock.close()

    @cached_property
    def model(self):
        options = self.ort.SessionOptions()
        options.intra_op_num_threads = positive_env('EMBEDDING_CPU_THREADS', 2)
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        options.graph_optimization_level = self.ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        return self.ort.InferenceSession(
            str(Path(self.DOWNLOAD_PATH) / self.EXTRACTED_FOLDER_NAME / 'model.onnx'),
            providers=['CPUExecutionProvider'], sess_options=options)


_embedding = None
_embedding_guard = threading.Lock()


def get_embedding_function() -> CPUEmbeddingFunction:
    """Load and validate once per process, before serving search requests."""
    global _embedding
    with _embedding_guard:
        if _embedding is None:
            embedding = CPUEmbeddingFunction()
            embedding(['模型预热'])
            logger.info('Embedding 已就绪: module=%s model=%s provider=CPUExecutionProvider',
                        __file__, embedding.DOWNLOAD_PATH)
            _embedding = embedding
        return _embedding
