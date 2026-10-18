"""Private local inference service. One model owner, JSON messages, no pickle."""
import gc
import json
import os
import signal
import time
from dataclasses import asdict, is_dataclass
from multiprocessing.connection import Client, Listener
from app.services.task_limits import runtime_dir, positive_env


def address():
    if os.name == 'posix':
        return str(runtime_dir() / 'inference.sock'), 'AF_UNIX'
    return r'\\.\pipe\bilinote-' + os.environ['TASK_INFERENCE_KEY'][:16], 'AF_PIPE'


def inference_call(operation, **kwargs):
    from app.utils.logger import get_logger
    last_error = None
    for attempt in range(2):
        try:
            return _inference_call_once(operation, **kwargs)
        except TimeoutError:
            raise
        except (OSError, EOFError) as exc:
            last_error = exc
            get_logger(__name__).warning('本地推理连接中断，等待服务恢复 attempt=%s', attempt + 1)
            time.sleep(1)
    raise RuntimeError('本地推理服务中断，请重试') from last_error


def _inference_call_once(operation, **kwargs):
    endpoint, family = address()
    deadline = time.monotonic() + 10
    while True:
        try:
            connection = Client(endpoint, family=family, authkey=bytes.fromhex(os.environ['TASK_INFERENCE_KEY']))
            break
        except (ConnectionRefusedError, FileNotFoundError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(.1)
    with connection:
        connection.send_bytes(json.dumps({'op': operation, **kwargs}).encode())
        if not connection.poll(positive_env('TASK_INFERENCE_TIMEOUT_SECONDS', 1800)):
            # Stop the model owner BEFORE the caller releases the GPU gate.
            info = json.loads((runtime_dir() / 'inference.json').read_text())
            if os.name == 'posix':
                os.killpg(info['pid'], signal.SIGKILL)
            else:
                import subprocess
                subprocess.run(['taskkill', '/PID', str(info['pid']), '/T', '/F'], capture_output=True)
            raise TimeoutError('本地推理超时，已停止推理进程')
        response = json.loads(connection.recv_bytes())
    if 'error' in response:
        raise RuntimeError(response['error'])
    return response.get('result')


class ModelOwner:
    def __init__(self):
        self.transcriber = None
        self.model_size = None

    @staticmethod
    def free_gpu_memory_mib():
        import subprocess
        try:
            result = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=2, check=True)
            return int(result.stdout.splitlines()[0].strip())
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return None

    @staticmethod
    def log_memory(stage):
        from app.utils.logger import get_logger
        import subprocess
        try:
            result = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.total',
                                     '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                get_logger(__name__).info('显存采样 stage=%s used_total_mib=%s', stage, result.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            pass

    def unload(self):
        if self.transcriber is not None:
            self.transcriber.model.model.unload_model()
            self.transcriber = None
            self.model_size = None
            gc.collect()
            self.log_memory('whisper_unloaded')

    def handle(self, request):
        if request['op'] == 'prepare_ocr':
            # Opt-in reuse still runs under the single GPU compute gate. Keep a
            # reserve for OCR workspaces, and unload if memory cannot be measured.
            if os.getenv('WHISPER_KEEP_DURING_OCR', '0') == '1' and self.transcriber is not None:
                free = self.free_gpu_memory_mib()
                if free is not None and free >= positive_env('OCR_GPU_RESERVE_MIB', 3072):
                    self.log_memory('whisper_retained_for_ocr')
                    return None
            self.unload()
            return None
        if request['op'] != 'transcribe':
            raise ValueError('未知推理操作')
        from app.transcriber.whisper import WhisperTranscriber
        model_size = request['model_size']
        if self.transcriber is None or self.model_size != model_size:
            self.unload()
            self.transcriber = WhisperTranscriber(model_size=model_size, device='cuda',
                cpu_threads=positive_env('WHISPER_CPU_THREADS', 2))
            self.model_size = model_size
        self.log_memory('whisper_loaded')
        result = self.transcriber.transcript(request['file_path'])
        self.log_memory('whisper_finished')
        result.raw = {**(asdict(result.raw) if is_dataclass(result.raw) else {}),
                      'device': self.transcriber.device, 'compute_type': getattr(self.transcriber, 'compute_type', None)}
        return asdict(result)


def serve():
    endpoint, family = address()
    if family == 'AF_UNIX':
        from pathlib import Path
        Path(endpoint).unlink(missing_ok=True)
    owner = ModelOwner()
    with Listener(endpoint, family=family, authkey=bytes.fromhex(os.environ['TASK_INFERENCE_KEY'])) as listener:
        (runtime_dir() / 'inference.json').write_text(json.dumps({'pid': os.getpid()}))
        while True:
            with listener.accept() as connection:
                try:
                    request = json.loads(connection.recv_bytes())
                    result = owner.handle(request)
                    connection.send_bytes(json.dumps({'result': result}).encode())
                except Exception as exc:
                    from app.utils.logger import get_logger
                    get_logger(__name__).exception('本地推理失败')
                    try:
                        owner.unload()
                    except Exception:
                        # Native errors can poison a CUDA context; exit instead of reusing it.
                        os._exit(1)
                    try:
                        connection.send_bytes(json.dumps({'error': str(exc)}).encode())
                    except (BrokenPipeError, EOFError, OSError):
                        pass


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    from app.services.task_process import watch_parent
    watch_parent(int(os.environ['TASK_PARENT_PID']))
    serve()
