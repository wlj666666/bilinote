"""Learn caption baselines across time; absence never means a layout switch."""
from statistics import median

from app.ocr.subtitle_tracker import locate


def _anchor(box, alignment):
    return box[0] if alignment == 'left' else box[2] if alignment == 'right' else (box[0]+box[2])/2


def matches_line(box, line, alignment=True):
    height = box[3]-box[1]
    ref_height = line['height']
    return (.72 <= height/max(ref_height, .001) <= 1.55
            and abs(box[3]-line['baseline']) <= ref_height*.32
            and (not alignment or abs(_anchor(box, line['alignment'])-line['anchor']) <= .055))


def full_box(box, band):
    x1, y1, x2, y2 = band
    return (x1+box[0]*(x2-x1), y1+box[1]*(y2-y1),
            x1+box[2]*(x2-x1), y1+box[3]*(y2-y1))


def matches_layout(box, band, layout, alignment=True):
    return any(matches_line(full_box(box, band), line, alignment) for line in layout['lines'])


def select_caption_boxes(boxes, band, layout, aspect_ratio):
    """Match the alignment of a whole line, including separately detected words."""
    selected = []
    for line_index, line in enumerate(layout['lines']):
        eligible = sorted((box for box in boxes if matches_line(full_box(box.box, band), line, False)),
                          key=lambda box: box.box[0])
        groups = []
        # Convert glyph height to horizontal coordinates using frame aspect ratio.
        margin = line['height']/max(aspect_ratio, .01)*2.
        for box in eligible:
            geometry = full_box(box.box, band)
            if groups and geometry[0]-groups[-1][-1][1][2] <= margin:
                groups[-1].append((box, geometry))
            else:
                groups.append([(box, geometry)])
        for group in groups:
            geometry = (min(g[1][0] for g in group), min(g[1][1] for g in group),
                        max(g[1][2] for g in group), max(g[1][3] for g in group))
            if matches_line(geometry, line):
                selected.extend((line_index, box) for box, _ in group)
    selected.sort(key=lambda item: (item[0], item[1].box[0]))
    return [box for _, box in selected]


def learn_layouts(samples, block_seconds=20, progress=None):
    """Aggregate dynamic tracks without treating their union boxes as glyph size.

    A static title alone cannot establish a caption model. Once established,
    however, that model also accepts static captions and one-character captions.
    """
    windows = {}
    for ts, boxes in samples:
        windows.setdefault(int(ts/block_seconds), []).append((ts, boxes))
    profiles = []
    candidates = {}
    for index, window in windows.items():
        if progress:
            progress('detect', .99)
        _, tracks, _ = locate(window)
        candidates[index] = tracks
        for track in tracks:
            if track['score'] < .28 or track['distinct'] < 2:
                continue
            geometry = track['geometry']
            # Similar baselines/font scales can still belong to different columns.
            profile = next((p for p in profiles if matches_line(geometry, p)), None)
            if profile is None:
                profile = dict(height=track['height'], baseline=track['baseline'],
                               alignment=track['alignment'], anchor=track['anchor'],
                               geometry=geometry, tracks=[], windows={}, times=set())
                profiles.append(profile)
            profile['tracks'].append(track)
            profile['windows'][index] = max(profile['windows'].get(index, 0.),
                                             track['score']*len(window)*2.)
            profile['times'].update(track['times'])
            for key in ('height', 'baseline'):
                profile[key] = median(t[key] for t in profile['tracks'])
            profile['geometry'] = tuple(median(t['geometry'][axis] for t in profile['tracks']) for axis in range(4))
            profile['anchor'] = median(_anchor(t['geometry'], profile['alignment']) for t in profile['tracks'])
    for profile in profiles:
        profile['support'] = sum(profile['windows'].values())
        # Small, constantly changing menu/status text must not beat larger
        # captions merely because it remains visible during a long pause.
        profile['rank_weight'] = (profile['support']*profile['height']
                                  *median(track['score'] for track in profile['tracks']))
    profiles.sort(key=lambda p: p['rank_weight'], reverse=True)
    if not profiles:
        return [], candidates
    # Keep meaningful alternatives, including short videos with only one window.
    minimum = profiles[0]['support']*.12
    profiles = [p for p in profiles if p['support'] >= minimum]
    layouts = []
    used = set()
    for i, profile in enumerate(profiles):
        if i in used:
            continue
        lines = [profile]
        for j, other in enumerate(profiles):
            if j <= i or j in used:
                continue
            overlap = len(profile['times'] & other['times'])/max(1, min(len(profile['times']), len(other['times'])))
            if (.7 <= other['height']/profile['height'] <= 1.4
                    and .8 <= abs(other['baseline']-profile['baseline'])/profile['height'] <= 2.3
                    and profile['alignment'] == other['alignment']
                    and abs(profile['anchor']-other['anchor']) <= .04
                    and other['support'] >= profile['support']*.55 and overlap >= .6):
                lines.append(other)
                used.add(j)
                break  # At most two lines; do not grow a band across code rows.
        serialized = [{key: line[key] for key in ('height', 'baseline', 'alignment', 'anchor', 'geometry')}
                      for line in sorted(lines, key=lambda p: p['baseline'])]
        y1 = min(line['baseline']-line['height']*1.4 for line in lines)
        y2 = max(line['baseline']+line['height']*.4 for line in lines)
        times = set().union(*(line['times'] for line in lines))
        layouts.append(dict(id=len(layouts), lines=serialized, roi=(0., max(0., y1), 1., min(1., y2)),
                            support=max(line['support'] for line in lines),
                            rank_weight=max(line['rank_weight'] for line in lines), times=sorted(times)))
    return layouts, candidates


def resolve_layouts(samples, duration, layouts, candidates, block_seconds=20):
    """Persist a band through blanks and switch only on sustained exclusive support.

    Regions that mostly coexist with the principal band are UI/competitors, not
    alternate layouts. Comparable simultaneous regions remain uncertain.
    """
    if not layouts:
        return []
    dominant = layouts[0]
    dominant_times = set(dominant['times'])
    alternatives = {dominant['id']}
    competitors = set()
    for layout in layouts[1:]:
        overlap = len(set(layout['times']) & dominant_times)/max(1, len(layout['times']))
        if overlap <= .35:
            alternatives.add(layout['id'])
        elif (layout['support'] >= dominant['support']*.7
              and layout['rank_weight'] >= dominant['rank_weight']*.7
              and .7 <= layout['lines'][0]['height']/dominant['lines'][0]['height'] <= 1.5):
            competitors.add(layout['id'])
    current = None
    pending = []
    decisions = []
    for ts, boxes in samples:
        present = [layout for layout in layouts if any(
            b.score >= .65 and matches_layout(b.box, (0., 0., 1., 1.), layout) for b in boxes)]
        ids = {layout['id'] for layout in present}
        if current is None:
            current = next((layout for layout in present if layout['id'] in alternatives), dominant)
        ambiguous = current['id'] in ids and bool(ids & competitors)
        if current['id'] in ids:
            pending = []
        else:
            alternate = next((layout for layout in present
                              if layout['id'] in alternatives and layout['id'] != current['id']), None)
            if alternate:
                if pending and pending[-1][1]['id'] != alternate['id']:
                    pending = []
                pending.append((len(decisions), alternate))
                if len(pending) >= 3:
                    current = alternate
                    # Offline confirmation permits correcting the onset, without
                    # emitting a wrong band while waiting for the next probes.
                    for pos, _ in pending:
                        if pos < len(decisions):
                            decisions[pos]['layout_id'] = current['id']
                            decisions[pos]['ambiguous'] = False
                    pending = []
            else:
                pending = []
        decisions.append(dict(time=ts, layout_id=current['id'], ambiguous=ambiguous))
    blocks = []
    for index, decision in enumerate(decisions):
        start = 0. if index == 0 else (decisions[index-1]['time']+decision['time'])/2
        end = duration if index == len(decisions)-1 else (decision['time']+decisions[index+1]['time'])/2
        layout = layouts[decision['layout_id']]
        window = int(decision['time']/block_seconds)
        # Keep bounded windows for diagnostics and later local review.
        if (blocks and blocks[-1]['layout_id'] == layout['id']
                and blocks[-1]['ambiguous'] == decision['ambiguous']
                and blocks[-1]['window'] == window):
            blocks[-1]['end'] = end
        else:
            blocks.append(dict(start=start, end=end, roi=layout['roi'], layout_id=layout['id'],
                               ambiguous=decision['ambiguous'], window=window,
                               candidates=candidates.get(window, []),
                               caption_references=[line['geometry'] for line in layout['lines']]))
    return blocks
