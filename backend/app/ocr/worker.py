"""Private subprocess entry point: local files in, JSON progress/report out."""
import argparse
import json
import time
from app.services.hard_subtitle_extractor import HardSubtitleExtractor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('video')
    parser.add_argument('report')
    parser.add_argument('--fps', type=float, required=True)
    parser.add_argument('--block-seconds', type=int, default=20)
    args = parser.parse_args()
    last = [None]
    last_sent = [0.]
    def progress(phase, fraction):
        key = (phase, int(fraction*100))
        now = time.monotonic()
        if key != last[0] or now-last_sent[0] >= 10:
            print('OCR_PROGRESS ' + json.dumps({'phase': phase, 'fraction': fraction}), flush=True)
            last[0] = key
            last_sent[0] = now
    progress('detect', 0.)
    result = HardSubtitleExtractor(fps=args.fps, block_seconds=args.block_seconds)._extract_direct(
        args.video, args.report, progress)
    return 0 if result.status == 'success' else 1


if __name__ == '__main__':
    raise SystemExit(main())
