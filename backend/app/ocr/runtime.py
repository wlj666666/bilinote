"""Select and verify the OCR inference device without silent CPU fallback."""
import os


def requested_device():
    device = os.getenv('OCR_DEVICE', 'cpu').strip().lower()
    if device not in {'cpu', 'cuda'}:
        raise ValueError('OCR_DEVICE 必须是 cpu 或 cuda')
    return device


def prepare_runtime(device):
    import onnxruntime as ort
    if device == 'cuda':
        # Load the pinned NVIDIA wheels before creating any inference sessions.
        # This also works in the clean interpreter started by the OCR worker.
        ort.preload_dlls(directory='')
        if 'CUDAExecutionProvider' not in ort.get_available_providers():
            raise RuntimeError('OCR GPU 不可用：请安装 requirements-ocr-gpu.txt；未自动切换 CPU')


def verify_runtime(engine, device):
    expected = 'CUDAExecutionProvider' if device == 'cuda' else 'CPUExecutionProvider'
    providers = {}
    for name in ('text_det', 'text_cls', 'text_rec'):
        session = getattr(engine, name).session.session
        actual = session.get_providers()
        if not actual or actual[0] != expected:
            raise RuntimeError(f'OCR {name} 未使用 {expected}，实际为 {actual}；请检查 CUDA 依赖和显卡驱动')
        # ORT can otherwise recreate a session on CPU after an EP execution error.
        session.disable_fallback()
        providers[name] = actual
    return {'device': device, 'providers': providers}
