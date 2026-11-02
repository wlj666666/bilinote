"""Compare OCR transcripts across concurrency configurations.

Answers one question: does raising the slot count change what OCR produces?
That is the veto criterion for enabling concurrency -- a throughput gain is
worthless if the recognised subtitles change.

Compares, per video and per segment index:
  * full text character-sequence similarity (difflib)
  * exact segment triple equality (start, end, text)
  * which segments appeared or disappeared
  * band_ocr_reads / visual_reused_frames, which show whether the pipeline
    made different internal decisions

Usage (from backend):
  python scripts/bench_ocr_compare.py \
      --baseline data/ocr_bench_2026-10-31/e2e-g1c1 \
      --candidate data/ocr_bench_2026-10-31/e2e-g2c2 \
      --output data/ocr_bench_2026-10-31/consistency-g2c2.json
"""
import argparse
from difflib import SequenceMatcher
import json
from pathlib import Path
import statistics


def load_runs(directory):
    """Collect every completed trial's transcripts from a config directory."""
    runs = []
    for path in sorted(Path(directory).glob('e2e-g*-c*-r*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('transcripts'):
            runs.append(data)
    return runs


def segment_triples(transcript):
    return [(round(float(s[0]), 3), round(float(s[1]), 3), s[2]) for s in transcript['segments']]


def compare_transcript(baseline, candidate):
    base_text = baseline.get('full_text') or ''
    cand_text = candidate.get('full_text') or ''
    base_segments = segment_triples(baseline)
    cand_segments = segment_triples(candidate)
    base_set, cand_set = set(base_segments), set(cand_segments)
    return {
        'text_similarity': round(SequenceMatcher(None, base_text, cand_text).ratio(), 6),
        'text_identical': base_text == cand_text,
        'segments_baseline': len(base_segments),
        'segments_candidate': len(cand_segments),
        'segments_identical': base_segments == cand_segments,
        'segments_only_in_baseline': len(base_set - cand_set),
        'segments_only_in_candidate': len(cand_set - base_set),
        'sample_only_in_baseline': sorted(base_set - cand_set)[:5],
        'sample_only_in_candidate': sorted(cand_set - base_set)[:5],
        'band_ocr_reads_baseline': baseline.get('band_ocr_reads'),
        'band_ocr_reads_candidate': candidate.get('band_ocr_reads'),
        'visual_reused_baseline': baseline.get('visual_reused_frames'),
        'visual_reused_candidate': candidate.get('visual_reused_frames'),
        'quality_baseline': baseline.get('quality_status'),
        'quality_candidate': candidate.get('quality_status'),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    baseline_runs = load_runs(args.baseline)
    candidate_runs = load_runs(args.candidate)
    if not baseline_runs or not candidate_runs:
        raise SystemExit('缺少可比较的实验结果')

    # Within-config stability first: if the same config already disagrees with
    # itself, a cross-config difference cannot be attributed to concurrency.
    videos = sorted(set.intersection(*[set(r['transcripts']) for r in baseline_runs]))
    stability = {}
    for video in videos:
        texts = [r['transcripts'][video]['full_text'] for r in baseline_runs]
        stability[video] = {
            'runs': len(texts),
            'identical_across_runs': len(set(texts)) == 1,
            'similarity_min': round(min(
                SequenceMatcher(None, texts[0], t).ratio() for t in texts), 6),
        }

    comparisons = {}
    for video in videos:
        base = baseline_runs[0]['transcripts'][video]
        rows = [compare_transcript(base, run['transcripts'][video])
                for run in candidate_runs if video in run['transcripts']]
        if not rows:
            continue
        comparisons[video] = {
            'against_runs': len(rows),
            'text_similarity_min': min(r['text_similarity'] for r in rows),
            'text_similarity_median': round(statistics.median(
                r['text_similarity'] for r in rows), 6),
            'all_text_identical': all(r['text_identical'] for r in rows),
            'all_segments_identical': all(r['segments_identical'] for r in rows),
            'segments_changed_max': max(
                r['segments_only_in_baseline'] + r['segments_only_in_candidate'] for r in rows),
            'band_ocr_reads_delta': [r['band_ocr_reads_candidate'] - r['band_ocr_reads_baseline']
                                     if None not in (r['band_ocr_reads_candidate'],
                                                     r['band_ocr_reads_baseline']) else None
                                     for r in rows],
            'details': rows,
        }

    verdict = {
        'all_videos_text_identical': all(c['all_text_identical'] for c in comparisons.values()),
        'all_videos_segments_identical': all(c['all_segments_identical'] for c in comparisons.values()),
        'min_text_similarity': min((c['text_similarity_min'] for c in comparisons.values()),
                                   default=None),
        'baseline_self_consistent': all(s['identical_across_runs'] for s in stability.values()),
    }
    report = {'baseline_dir': str(Path(args.baseline).resolve()),
              'candidate_dir': str(Path(args.candidate).resolve()),
              'scope': 'same videos, same OCR pipeline; only slot count differs',
              'baseline_stability': stability, 'comparisons': comparisons, 'verdict': verdict}

    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    print(json.dumps({'verdict': verdict}, ensure_ascii=False, indent=2))
    for video, row in comparisons.items():
        print(f'{video}: sim_min={row["text_similarity_min"]:.4f} '
              f'text_same={row["all_text_identical"]} '
              f'seg_same={row["all_segments_identical"]} '
              f'seg_changed_max={row["segments_changed_max"]}')
    print(f'written: {out}')


if __name__ == '__main__':
    main()
