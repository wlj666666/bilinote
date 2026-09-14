import sys
from types import SimpleNamespace
from unittest.mock import Mock

from app.utils import env_checker


def fake_engine(monkeypatch, count=1):
    engine = SimpleNamespace(
        get_cuda_device_count=lambda: count,
        get_supported_compute_types=lambda device: {"float16", "int8_float16"},
    )
    monkeypatch.setitem(sys.modules, "ctranslate2", engine)
    monkeypatch.setattr(env_checker.sys, "platform", "linux")


def test_cuda_detection_does_not_require_torch(monkeypatch):
    fake_engine(monkeypatch)
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setattr(env_checker.ctypes, "CDLL", Mock())
    monkeypatch.setattr(env_checker.importlib.metadata, "version", lambda name: "12.8.4.1")
    monkeypatch.setattr(env_checker.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="Test GPU\n"))
    status = env_checker.get_cuda_status()
    assert status["available"] is True
    assert status["version"] == "12.8"


def test_missing_runtime_library_is_not_reported_as_available(monkeypatch):
    fake_engine(monkeypatch)
    monkeypatch.setattr(env_checker.ctypes, "CDLL", Mock(side_effect=OSError("missing libcublas")))
    status = env_checker.get_cuda_status()
    assert not status["available"]
    assert "libcublas" in status["error"]


def test_cpu_only_host_remains_supported(monkeypatch):
    fake_engine(monkeypatch, count=0)
    loader = Mock()
    monkeypatch.setattr(env_checker.ctypes, "CDLL", loader)
    assert not env_checker.is_cuda_available()
    loader.assert_not_called()


def test_model_load_failure_preserves_cache(monkeypatch):
    from app.transcriber.whisper import WhisperTranscriber
    import pytest

    purge = Mock()
    monkeypatch.setattr(WhisperTranscriber, "_purge_cache", purge)
    monkeypatch.setattr(WhisperTranscriber, "_build_model", Mock(side_effect=RuntimeError("CUDA failure")))
    with pytest.raises(RuntimeError, match="CUDA failure"):
        WhisperTranscriber("large-v3", device="cpu")
    purge.assert_not_called()
