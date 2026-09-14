import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.ocr.runtime import requested_device, prepare_runtime, verify_runtime


def engine_with(providers):
    engine = SimpleNamespace()
    for name, values in zip(('text_det', 'text_cls', 'text_rec'), providers):
        session = Mock(get_providers=Mock(return_value=values))
        setattr(engine, name, SimpleNamespace(session=SimpleNamespace(session=session)))
    return engine


def test_explicit_device_selection(monkeypatch):
    monkeypatch.delenv('OCR_DEVICE', raising=False)
    assert requested_device() == 'cpu'
    monkeypatch.setenv('OCR_DEVICE', 'CUDA')
    assert requested_device() == 'cuda'
    monkeypatch.setenv('OCR_DEVICE', 'auto')
    with pytest.raises(ValueError):
        requested_device()


def test_gpu_request_cannot_silently_use_cpu(monkeypatch):
    ort = SimpleNamespace(preload_dlls=Mock(), get_available_providers=lambda: ['CPUExecutionProvider'])
    monkeypatch.setitem(sys.modules, 'onnxruntime', ort)
    with pytest.raises(RuntimeError, match='未自动切换 CPU'):
        prepare_runtime('cuda')
    ort.preload_dlls.assert_called_once_with(directory='')


def test_cpu_does_not_require_cuda_libraries(monkeypatch):
    ort = SimpleNamespace(preload_dlls=Mock(side_effect=AssertionError('No CUDA loading')))
    monkeypatch.setitem(sys.modules, 'onnxruntime', ort)
    prepare_runtime('cpu')


@pytest.mark.parametrize('failed_model', range(3))
def test_each_actual_model_session_is_verified(failed_model):
    providers = [['CUDAExecutionProvider', 'CPUExecutionProvider'] for _ in range(3)]
    providers[failed_model] = ['CPUExecutionProvider']
    with pytest.raises(RuntimeError, match='未使用 CUDAExecutionProvider'):
        verify_runtime(engine_with(providers), 'cuda')


def test_cuda_sessions_disable_runtime_fallback_and_record_providers():
    engine = engine_with([['CUDAExecutionProvider', 'CPUExecutionProvider']] * 3)
    result = verify_runtime(engine, 'cuda')
    assert result['device'] == 'cuda'
    assert len(result['providers']) == 3
    for name in result['providers']:
        getattr(engine, name).session.session.disable_fallback.assert_called_once()
