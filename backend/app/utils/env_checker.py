"""CUDA detection for the actual faster-whisper inference engine."""
import ctypes
import importlib.metadata
import subprocess
import sys


def get_cuda_status() -> dict:
    status = {
        "available": False, "version": None, "gpu_name": None,
        "engine": "CTranslate2", "error": None,
    }
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() == 0:
            status["error"] = "CTranslate2 未检测到 CUDA 设备"
            return status
        ctranslate2.get_supported_compute_types("cuda")
        if sys.platform == "linux":
            ctypes.CDLL("libcublas.so.12")
            ctypes.CDLL("libcudnn.so.9")
        status["available"] = True
    except Exception as exc:
        status["error"] = str(exc)
        return status
    try:
        status["version"] = ".".join(
            importlib.metadata.version("nvidia-cublas-cu12").split(".")[:2]
        )
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3, check=True,
        )
        status["gpu_name"] = result.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        status["gpu_name"] = "CUDA GPU"
    return status


def is_cuda_available() -> bool:
    return get_cuda_status()["available"]
