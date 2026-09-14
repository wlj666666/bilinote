"""Extract a local video's hard subtitles without downloading media or calling an LLM.

python scripts/check_hard_subtitles.py VIDEO --output /tmp/subtitle-check.json
"""
import argparse
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.hard_subtitle_extractor import HardSubtitleExtractor, atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video')
    parser.add_argument('--output', required=True)
    parser.add_argument('--fps', type=float, default=5)
    parser.add_argument('--device', choices=['cpu', 'cuda'])
    args = parser.parse_args()
    if args.device:
        os.environ['OCR_DEVICE'] = args.device
    started = time.monotonic()
    last = [None]
    phases = {}
    def progress(phase, fraction):
        phases.setdefault(phase, time.monotonic())
        key = (phase, int(fraction*100)//5)
        if key != last[0]:
            print(f'{phase}: {fraction:.0%}', flush=True)
            last[0] = key
    result = HardSubtitleExtractor(fps=args.fps).extract(args.video, args.output, progress)
    finished = time.monotonic()
    timings = {'total_seconds': round(finished-started, 3)}
    if 'extract' in phases:
        timings.update(detect_seconds=round(phases['extract']-started, 3),
                       extract_seconds=round(finished-phases['extract'], 3))
    result.diagnostics['benchmark'] = timings
    atomic_json(args.output, result.diagnostics)
    print(f'{result.status}: {result.message}; timings={timings}', flush=True)
    if result.transcript:
        print(f'segments={len(result.transcript.segments)}')
        print(result.transcript.full_text[:500])
    return 0 if result.status == 'success' else 1


if __name__ == '__main__':
    raise SystemExit(main())
