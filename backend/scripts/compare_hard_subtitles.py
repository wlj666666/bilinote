"""Compare task-local OCR diagnostics and optional manually checked timestamps.

python scripts/compare_hard_subtitles.py OLD.json NEW.json --reference points.json --output comparison.json
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ocr.subtitle_tracker import caption_key
from app.services.hard_subtitle_extractor import atomic_json


def metrics(report):
    segments = (report.get('transcript') or {}).get('segments', [])
    return dict(status=report.get('status'), duration=report.get('duration'),
                segment_count=len(segments),
                recognized_seconds=round(sum(segment['end']-segment['start'] for segment in segments), 3),
                text_gap_seconds=round(sum(end-start for start,end in report.get('text_gaps', [])), 3),
                missing_region_seconds=round(report.get('missing_region_seconds', 0.), 3),
                unresolved_gap_seconds=report.get('unresolved_gap_seconds'),
                verified_no_subtitle_seconds=report.get('verified_no_subtitle_seconds'),
                low_score_fraction=report.get('low_score_fraction'),
                timings=report.get('benchmark', report.get('timings')))


def text_at(report, ts):
    return ' '.join(segment['text'] for segment in (report.get('transcript') or {}).get('segments', [])
                    if segment['start'] <= ts < segment['end'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('old')
    parser.add_argument('new')
    parser.add_argument('--reference', help='JSON array of {time, expected} checked against the video')
    parser.add_argument('--output')
    args = parser.parse_args()
    reports = [json.loads(Path(path).read_text(encoding='utf-8')) for path in (args.old, args.new)]
    comparison = dict(old=metrics(reports[0]), new=metrics(reports[1]))
    if args.reference:
        points = json.loads(Path(args.reference).read_text(encoding='utf-8'))
        checks = []
        for point in points:
            texts = [text_at(report, point['time']) for report in reports]
            expected = caption_key(point['expected']).replace(' ', '')
            checks.append({**point, 'old_text': texts[0], 'new_text': texts[1],
                           'old_matches': caption_key(texts[0]).replace(' ', '') == expected,
                           'new_matches': caption_key(texts[1]).replace(' ', '') == expected})
        comparison['reference_checks'] = checks
    if args.output:
        atomic_json(args.output, comparison)
    print(json.dumps(comparison, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
