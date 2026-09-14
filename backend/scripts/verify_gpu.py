"""Offline GPU acceptance check using a complete local recording.

Run: bash start-gpu.sh scripts/verify_gpu.py data/data/VIDEO.mp3
Writes results under data/gpu_validation; never calls a paid API.
"""
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

os.environ["HF_HUB_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faster_whisper.audio import decode_audio
from app.transcriber.whisper import WhisperTranscriber
from app.utils.env_checker import get_cuda_status


def main():
    source = Path(sys.argv[1]).resolve()
    output = Path("data/gpu_validation")
    output.mkdir(parents=True, exist_ok=True)
    status = get_cuda_status()
    print(json.dumps(status, ensure_ascii=False), flush=True)
    assert status["available"], status
    audio = decode_audio(str(source), sampling_rate=16000)
    duration = len(audio) / 16000
    assert duration > 30, "Use a recording longer than 30 seconds"
    samples = []
    stopped = threading.Event()

    def monitor():
        while not stopped.is_set():
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                samples.append([int(v.strip()) for v in result.stdout.splitlines()[0].split(",")])
            stopped.wait(1)

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    report = {"source": str(source), "audio_seconds": duration, "cuda": status}
    try:
        started = time.perf_counter()
        gpu = WhisperTranscriber("large-v3", device="cuda")
        report["gpu_load_seconds"] = time.perf_counter() - started
        assert gpu.model.model.device == "cuda"
        report["gpu_compute_type"] = gpu.model.model.compute_type
        started = time.perf_counter()
        transcript = gpu.transcript(str(source))
        report["gpu_full_seconds"] = time.perf_counter() - started
        report["segment_count"] = len(transcript.segments)
        report["last_segment_end"] = transcript.segments[-1].end
        assert transcript.full_text and report["last_segment_end"] >= duration - 15
        (output / "gpu_transcript.json").write_text(json.dumps({
            "language": transcript.language, "full_text": transcript.full_text,
            "segments": [vars(s) for s in transcript.segments],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        clip = audio[:30 * 16000]
        started = time.perf_counter()
        segments, _ = gpu.model.transcribe(clip)
        list(segments)  # Actual inference happens while consuming the iterator.
        report["gpu_30s_seconds"] = time.perf_counter() - started
        del gpu
        gc.collect()
    finally:
        stopped.set()
        watcher.join(timeout=6)
    report["peak_total_gpu_memory_mib"] = max(s[0] for s in samples) if samples else None
    report["peak_gpu_utilization_percent"] = max(s[1] for s in samples) if samples else None
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    cpu = WhisperTranscriber("large-v3", device="cpu")
    started = time.perf_counter()
    segments, _ = cpu.model.transcribe(clip)
    list(segments)
    report["cpu_30s_seconds"] = time.perf_counter() - started
    report["clip_speedup"] = report["cpu_30s_seconds"] / report["gpu_30s_seconds"]
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
