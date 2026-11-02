#!/usr/bin/env bash
# Run the OCR-only end-to-end concurrency matrix sequentially.
# Both configurations use the same videos so the only variable is slot count.
set -u
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

PY="${BILINOTE_PYTHON:-python}"
OUT=data/ocr_bench_2026-10-31
CLIPS="$OUT/clips/editor_gaps-60s.mp4 $OUT/clips/landscape_game-120s.mp4 $OUT/clips/portrait_speech-180s.mp4 $OUT/clips/landscape_fourth-300s.mp4"
LOG="$OUT/logs"

mkdir -p "$LOG"

run() {
  local gpu="$1" cpu="$2" out="$3"
  echo "=== start gpu=$gpu cpu=$cpu -> $out $(date +%H:%M:%S) ==="
  BILINOTE_PYTHON="$PY" bash start-gpu.sh scripts/bench_ocr_e2e.py \
    --output "$OUT/$out" --gpu-slots "$gpu" --cpu-slots "$cpu" --repeat 3 \
    --videos $CLIPS > "$LOG/$out.log" 2>&1
  echo "=== done gpu=$gpu cpu=$cpu exit=$? $(date +%H:%M:%S) ==="
}

# Baseline: one OCR at a time, which is what serving does today.
run 1 1 e2e-g1c1
# One extra overlapping OCR process. cpu must rise too: each OCR process
# occupies a full core, and a GPU stage also needs a CPU slot.
run 2 2 e2e-g2c2
# Isolate whether the gain is GPU-bound or CPU-bound: more GPU slots than
# CPU slots cannot be used, so this also documents the current ceiling.
run 2 1 e2e-g2c1

echo "=== all done $(date +%H:%M:%S) ==="
