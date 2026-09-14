#!/usr/bin/env bash
# Run after `conda activate bilinote`. Also accepts a Python script for checks.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
gpu_python="${BILINOTE_PYTHON:-python}"
gpu_libs="$("$gpu_python" - <<'PY'
from importlib.metadata import distribution
from pathlib import Path
paths = []
for package, folder in [("nvidia-cublas-cu12", "cublas"), ("nvidia-cudnn-cu12", "cudnn")]:
    path = Path(distribution(package).locate_file(f"nvidia/{folder}/lib"))
    if not path.is_dir():
        raise SystemExit(f"Missing CUDA library directory: {path}")
    paths.append(str(path))
print(":".join(paths))
PY
)"
export LD_LIBRARY_PATH="$gpu_libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export WHISPER_CUDA_COMPUTE_TYPE="${WHISPER_CUDA_COMPUTE_TYPE:-int8_float32}"
export OCR_DEVICE="${OCR_DEVICE:-cuda}"
export TASK_MAX_WORKERS="${TASK_MAX_WORKERS:-1}"
if (( $# )); then
    exec "$gpu_python" "$@"
else
    exec "$gpu_python" main.py
fi
