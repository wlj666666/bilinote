"""Bounded subprocess execution for native OCR/codec workloads."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def run_worker(video_path, diagnostic_path, fps, block_seconds, progress=None):
    from app.models.transcriber_model import TranscriptResult, TranscriptSegment
    from app.services.hard_subtitle_extractor import ExtractionResult, atomic_json
    path = Path(diagnostic_path).resolve()
    process = None
    reader = None
    try:
        stall_timeout = float(os.getenv('OCR_STALL_TIMEOUT_SECONDS', '120'))
        if not 15 <= stall_timeout <= 3600:
            raise ValueError('OCR_STALL_TIMEOUT_SECONDS 必须在 15 到 3600 之间')
        # Do not let a previous successful/partial report stand in for a failed worker.
        atomic_json(path, {'status': 'starting'})
        process = subprocess.Popen([
            sys.executable, '-m', 'app.ocr.worker', str(Path(video_path).resolve()), str(path),
            '--fps', str(fps), '--block-seconds', str(block_seconds),
        ], cwd=str(Path(__file__).resolve().parents[2]), stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        messages = queue.Queue()
        def read_output():
            try:
                for line in process.stdout:
                    messages.put(line)
            finally:
                messages.put(None)
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        last_progress = time.monotonic()
        last_error = ''
        while True:
            try:
                line = messages.get(timeout=min(2., stall_timeout))
            except queue.Empty:
                if time.monotonic()-last_progress > stall_timeout:
                    raise TimeoutError(f'OCR 连续 {stall_timeout:g} 秒无进度，已停止；请重试或改用语音转写')
                continue
            if line is None:
                break
            if line.startswith('OCR_PROGRESS '):
                event = json.loads(line[len('OCR_PROGRESS '):])
                last_progress = time.monotonic()
                if progress:
                    progress(event['phase'], event['fraction'])
            else:
                last_error = line.strip()[-500:]
                if time.monotonic()-last_progress > stall_timeout:
                    raise TimeoutError('OCR 长时间无有效进度，已停止；请重试或改用语音转写')
        code = process.wait(timeout=5)
        report = json.loads(path.read_text(encoding='utf-8'))
        status = report.get('status')
        if status not in {'success', 'no_subtitles', 'uncertain', 'failed'} or (code != 0 and status == 'success'):
            raise RuntimeError(f'OCR 子进程异常退出（{code}）：{last_error}')
        data = report.get('transcript')
        transcript = (TranscriptResult(data.get('language'), data['full_text'],
                      [TranscriptSegment(**s) for s in data['segments']], data.get('raw')) if data else None)
        return ExtractionResult(status, transcript, report, report.get('message', ''))
    except Exception as exc:
        report = {'status': 'failed', 'message': str(exc)}
        atomic_json(path, report)
        return ExtractionResult('failed', None, report, str(exc))
    finally:
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if reader:
                reader.join(timeout=2)
            if process.stdout:
                process.stdout.close()
