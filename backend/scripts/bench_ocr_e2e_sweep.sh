#!/usr/bin/env bash
# Sweep OCR-only end-to-end concurrency until it stops being worthwhile.
#
# Every configuration uses the SAME task list so slot count is the only
# variable. 8 tasks (4 clips x 2) are used so levels above 4 are actually
# reachable: with only 4 tasks, c5 and c6 could never run in parallel.
#
# CPU slots track GPU slots because every GPU inference stage also takes a CPU
# slot (see backend/app/services/task_resources.py). Raising GPU alone was
# measured to have no effect.
set -u
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

PY="${BILINOTE_PYTHON:-python}"
OUT=data/ocr_bench_2026-10-31
LOG="$OUT/logs"
mkdir -p "$LOG"

# Long and short, three resolutions. Repeated twice to give 8 tasks, which is
# what makes levels 5-6 measurable at all.
BASE="$OUT/clips/editor_gaps-60s.mp4 $OUT/clips/landscape_game-120s.mp4 $OUT/clips/portrait_speech-180s.mp4 $OUT/clips/landscape_fourth-300s.mp4"
TASKS="$BASE $BASE"

run() {
  local level="$1" repeats="$2"
  local out="sweep-g${level}c${level}"
  if [ -f "$OUT/$out/e2e-g${level}-c${level}-summary.json" ]; then
    echo "=== skip gpu=$level cpu=$level (already complete) ==="
    return 0
  fi
  echo "=== start gpu=$level cpu=$level repeats=$repeats -> $out $(date +%H:%M:%S) ==="
  BILINOTE_PYTHON="$PY" bash start-gpu.sh scripts/bench_ocr_e2e.py \
    --output "$OUT/$out" --gpu-slots "$level" --cpu-slots "$level" \
    --repeat "$repeats" --timeout 3600 --videos $TASKS > "$LOG/$out.log" 2>&1
  local code=$?
  echo "=== done gpu=$level cpu=$level exit=$code $(date +%H:%M:%S) ==="
  if [ "$code" -ne 0 ] && [ ! -f "$OUT/$out/e2e-g${level}-c${level}-summary.json" ]; then
    # Preserve why this level failed instead of silently omitting it, so the
    # sweep continues and the failure point stays documented.
    local reason
    reason=$(grep -oE '显存超过91%[^"]*|温度达到[0-9]+C|实验超时|trial_failed' "$LOG/$out.log" 2>/dev/null | head -1)
    printf '{\n  "level": %s,\n  "reason": "%s",\n  "log": "%s",\n  "note": "该档位未产出汇总，不纳入曲线；原始日志保留"\n}\n' \
      "$level" "${reason:-unknown}" "$LOG/$out.log" > "$OUT/$out/excluded.json"
    echo "=== recorded excluded.json for level $level: ${reason:-unknown} ==="
  fi
  return 0
}

# All levels rerun on the 8-task list so the whole curve is directly comparable.
for level in 1 2 3 4 5 6 7 8; do
  run "$level" 3
done

echo "=== sweep done $(date +%H:%M:%S) ==="
