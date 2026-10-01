"""Evidence-based gap classification and bounded local review planning."""
from collections import Counter


def text_gaps(segments, duration, minimum=2.):
    gaps = []
    cursor = 0.
    for segment in segments:
        if segment.start-cursor >= minimum-1e-6:
            gaps.append((cursor, segment.start))
        cursor = segment.end
    if duration-cursor >= minimum-1e-6:
        gaps.append((cursor, duration))
    return gaps


def classify_gaps(segments, duration, evidence, step):
    gaps = []
    for start, end in text_gaps(segments, duration):
        frames = [frame for frame in evidence.values() if start-1e-6 <= frame['time'] < end-1e-6]
        states = Counter(frame['state'] for frame in frames)
        # Absence must be checked across the interval, not inferred from a few
        # successful reads. Detection failures/low scores never count as absence.
        coverage = len(frames)*step/max(end-start, step)
        absent = states.get('verified_blank', 0)
        if frames and absent == len(frames) and coverage >= .9:
            kind = 'no_subtitles'
        elif states.get('ambiguous') or states.get('unlocated'):
            kind = 'layout_uncertain'
        elif states.get('low_score') or states.get('detected_unreadable'):
            kind = 'recognition_failed'
        else:
            kind = 'insufficient_evidence'
        gaps.append(dict(start=start, end=end, kind=kind, frame_states=dict(states)))
    return gaps


def plan_review(gaps, duration, step, budget_fraction=.25):
    """Review serious gaps first; decoding/inference work is explicitly bounded."""
    budget = max(30., duration*budget_fraction)
    selected = []
    cost = 0.
    for gap in sorted(gaps, key=lambda g: (g['kind'] == 'insufficient_evidence', -(g['end']-g['start']))):
        if gap['kind'] == 'no_subtitles':
            continue
        start, end = max(0., gap['start']-step), min(duration, gap['end']+step)
        if cost+end-start > budget:
            continue
        selected.append((start, end))
        cost += end-start
    merged = []
    for start, end in sorted(selected):
        if merged and start <= merged[-1][1]+step:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def quality_summary(gaps, evidence, step, duration):
    seconds = Counter()
    for gap in gaps:
        seconds[gap['kind']] += gap['end']-gap['start']
    unresolved = sum(value for key, value in seconds.items() if key != 'no_subtitles')
    states = Counter(frame['state'] for frame in evidence.values())
    recognized_frames = states.get('text', 0)+states.get('low_score', 0)+states.get('detected_unreadable', 0)
    low_fraction = (states.get('low_score', 0)+states.get('detected_unreadable', 0))/max(1, recognized_frames)
    ambiguous_seconds = (states.get('ambiguous', 0)+states.get('unlocated', 0))*step
    longest = max((g['end']-g['start'] for g in gaps if g['kind'] != 'no_subtitles'), default=0.)
    reasons = []
    if unresolved > max(3., duration*.15):
        reasons.append('unresolved_gaps')
    if longest > max(20., duration*.05):
        reasons.append('continuous_unresolved_gap')
    if ambiguous_seconds > max(20., duration*.15):
        reasons.append('layout_uncertain')
    if low_fraction > .15:
        reasons.append('recognition_unstable')
    return dict(gap_seconds_by_kind=dict(seconds), verified_no_subtitle_seconds=seconds.get('no_subtitles', 0.),
                unresolved_gap_seconds=unresolved, longest_unresolved_gap_seconds=longest,
                low_score_fraction=low_fraction, missing_region_seconds=ambiguous_seconds,
                quality_reasons=reasons)
