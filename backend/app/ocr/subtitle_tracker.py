"""Temporal subtitle localization and conservative adjacent-caption merging."""
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
from statistics import median
import unicodedata

from app.models.transcriber_model import TranscriptSegment
from app.ocr.ocr_engine import TextBox


def normalized(text):
    return "".join(c.lower() for c in text if c.isalnum())


def caption_key(text):
    # Operators and punctuation may carry meaning (C vs C++, > vs <).
    return ' '.join(unicodedata.normalize('NFKC', text).split())


def similarity(a, b):
    return SequenceMatcher(None, normalized(a), normalized(b), autojunk=False).ratio()


def within_caption_band(box):
    """Exclude tall shapes cut by the padded band's edge (often clothes/faces).

    A localized single subtitle line occupies about 1/1.8 of the padded band.
    Apply only to isolated ASCII glyphs; full lines may expand towards an edge.
    A real standalone C or 3 centered in the band is still valid.
    """
    _, top, _, bottom = box.box
    key = caption_key(box.text)
    singleton = len(key) == 1 and key.isascii() and key.isalnum()
    return not (singleton and bottom-top > .62 and (top < .12 or bottom > .94))


@dataclass
class Track:
    observations: list = field(default_factory=list)

    def matches(self, item):
        old = self.observations[-1][1].box
        new = item.box
        h = max(old[3] - old[1], new[3] - new[1])
        # Match text baselines, allowing centered and left-aligned captions to grow.
        return (abs(old[3] - new[3]) < max(.008, h * .65)
                and .45 < (new[3] - new[1]) / max(old[3] - old[1], .001) < 2.2
                and min(abs(old[0] - new[0]), abs(sum(old[::2]) - sum(new[::2])) / 2) < .16)

    def metrics(self, samples):
        texts = [b.text for _, b in self.observations]
        groups = []
        for text in texts:
            if not any(similarity(text, old) >= .78 for old in groups):
                groups.append(text)
        presence = len(set(t for t, _ in self.observations)) / max(samples, 1)
        confidence = sum(b.score for _, b in self.observations) / max(len(texts), 1)
        # Static text and OCR jitter cannot win solely by having many detections.
        score = presence * confidence * min(max(len(groups) - 1, 0) / 3, 1)
        centers = [(b.box[0]+b.box[2])/2 for _, b in self.observations]
        lefts = [b.box[0] for _, b in self.observations]
        if centers and max(centers)-min(centers) > .20 and max(lefts)-min(lefts) > .20:
            score = 0.  # horizontally moving comments, not stationary subtitles
        return score, len(groups), presence


def locate(samples):
    """Return ROI and diagnostic candidates. None means no reliable dynamic track."""
    tracks = []
    for ts, boxes in samples:
        used = set()
        for item in sorted(boxes, key=lambda b: b.score, reverse=True):
            x1, y1, x2, y2 = item.box
            if item.score < .65 or len(normalized(item.text)) < 2 or not .008 <= y2-y1 <= .13:
                continue
            match = next((i for i, tr in enumerate(tracks) if i not in used and tr.matches(item)), None)
            if match is None:
                tracks.append(Track())
                match = len(tracks)-1
            tracks[match].observations.append((ts, item))
            used.add(match)
    ranked = sorted(((t.metrics(len(samples))[0], t) for t in tracks), key=lambda x: x[0], reverse=True)
    candidates = []
    for score, track in ranked:
        boxes = [b.box for _, b in track.observations]
        roi = (min(b[0] for b in boxes), min(b[1] for b in boxes),
               max(b[2] for b in boxes), max(b[3] for b in boxes))
        candidates.append({"score": round(score, 3), "roi": roi,
                           "examples": [b.text for _, b in track.observations][:6],
                           "distinct": track.metrics(len(samples))[1],
                           "times": sorted(set(t for t, _ in track.observations))})
    if not candidates or candidates[0]["score"] < .28:
        return None, candidates, False
    best = candidates[0]
    x1, y1, x2, y2 = best["roi"]
    h = y2-y1
    ambiguous = False
    for other in candidates[1:]:
        if other["score"] < max(.28, best["score"] * .8):
            continue
        ox1, oy1, ox2, oy2 = other["roi"]
        if abs((oy1+oy2-y1-y2)/2) < h*2.5 and abs((ox1+ox2-x1-x2)/2) < .18:
            y1, y2 = min(y1, oy1), max(y2, oy2)  # two-line captions
        else:
            ambiguous = True
    # Full horizontal band tolerates longer captions not seen in the probes.
    return (0., max(0., y1-h*.4), 1., min(1., y2+h*.4)), candidates, ambiguous


def merge_observations(observations, step, duration):
    """Merge only temporally adjacent equal text; preserve numbers/negations."""
    segments = []
    scores = []
    for ts, text, score in observations:
        text = text.strip()
        if not text:
            continue
        end = min(duration, ts + step)
        if segments and ts - segments[-1].end <= step*.25 and caption_key(segments[-1].text) == caption_key(text):
            segments[-1].end = end
            if score > scores[-1]:
                segments[-1].text, scores[-1] = text, score
        else:
            segments.append(TranscriptSegment(start=max(0., ts), end=end, text=text))
            scores.append(score)
    # One-sample variants surrounded by identical captions are OCR flicker.
    result = []
    i = 0
    while i < len(segments):
        if (i+2 < len(segments) and caption_key(segments[i].text) == caption_key(segments[i+2].text)
                and segments[i+1].end-segments[i+1].start <= step*1.01
                and segments[i+2].start-segments[i].end <= step*1.1
                and similarity(segments[i].text, segments[i+1].text) >= .85
                and not re.search(r'\d|不|没|无|非|[+<>=]', segments[i+1].text)):
            segments[i].end = segments[i+2].end
            result.append(segments[i]); i += 3
        else:
            result.append(segments[i]); i += 1
    for left, right in zip(result, result[1:]):
        left.end = min(left.end, right.start)
    return result


def locate_windows(samples, start, end):
    """Split competing layouts in time; simultaneous competitors remain uncertain."""
    roi, candidates, ambiguous = locate(samples)
    sequential = False
    if candidates:
        top_times = set(candidates[0]['times'])
        for candidate in candidates[1:]:
            other_times = set(candidate['times'])
            if candidate['score'] > .1 and not top_times.intersection(other_times):
                sequential = True
                break
    if (ambiguous or sequential) and len(samples) >= 4:
        middle = len(samples)//2
        boundary = (samples[middle-1][0]+samples[middle][0])/2
        return (locate_windows(samples[:middle], start, boundary)
                + locate_windows(samples[middle:], boundary, end))
    return [{"start": start, "end": end, "roi": roi,
             "ambiguous": ambiguous, "candidates": candidates}]


def stabilize_ambiguous_blocks(blocks):
    """Recover captions from a stable band when editor text competes with them.

    Require strong agreement across the video and reliable blocks on both sides.
    If the caption is absent, leave the interval uncertain rather than reading UI text.
    """
    reliable = [(index, block) for index, block in enumerate(blocks)
                if block.get('roi') and not block.get('ambiguous') and block.get('candidates')]
    if len(reliable) < 5:
        return blocks
    centers = [(block['roi'][1] + block['roi'][3]) / 2 for _, block in reliable]
    durations = [block['end'] - block['start'] for _, block in reliable]
    total = sum(durations)
    if total < 60:
        return blocks
    anchor = max(centers, key=lambda center: sum(
        duration for other, duration in zip(centers, durations) if abs(other-center) <= .04))
    stable = [(index, block) for (index, block), center in zip(reliable, centers)
              if abs(center-anchor) <= .04]
    stable_seconds = sum(block['end'] - block['start'] for _, block in stable)
    if len(stable) < 5 or stable_seconds < total * .6:
        return blocks
    anchor = median((block['roi'][1] + block['roi'][3]) / 2 for _, block in stable)
    reference = [block['candidates'][0]['roi'] for _, block in stable]
    height = median(box[3]-box[1] for box in reference)
    width = median(box[2]-box[0] for box in reference)
    x_center = median((box[0]+box[2])/2 for box in reference)
    typical_box = tuple(median(box[axis] for box in reference) for axis in range(4))
    typical_band = tuple(median(block['roi'][axis] for _, block in stable) for axis in range(4))

    def looks_like_caption(candidate):
        x1, y1, x2, y2 = candidate['roi']
        return (candidate['score'] >= .28 and abs((y1+y2)/2-anchor) <= .035
                and y2-y1 >= max(.02, height*.7)
                and x2-x1 >= max(.08, width*.3)
                and abs((x1+x2)/2-x_center) <= .23)

    trusted = [(index, block) for index, block in stable
               if looks_like_caption(block['candidates'][0])]
    if len(trusted) < 5 or sum(block['end']-block['start'] for _, block in trusted) < total*.5:
        return blocks

    for index, block in enumerate(blocks):
        before = next((good for pos, good in reversed(trusted) if pos < index), None)
        after = next((good for pos, good in trusted if pos > index), None)
        if before is None or after is None:
            continue
        before_gap = block['start']-before['end']
        after_gap = after['start']-block['end']
        if not block.get('ambiguous'):
            candidate = block['candidates'][0] if block.get('candidates') else None
            if candidate and abs((block['roi'][1]+block['roi'][3])/2-anchor) <= .04 and looks_like_caption(candidate):
                continue
            limit = 60 if abs((block['roi'][1]+block['roi'][3])/2-anchor) <= .04 else 25
            if before_gap <= limit and after_gap <= limit:
                block['roi'] = typical_band
                block['caption_candidate'] = typical_box
                block['profile_override'] = True
            continue
        if before_gap > 60 or after_gap > 60:
            continue
        matches = []
        for candidate in block.get('candidates', []):
            if looks_like_caption(candidate):
                matches.append(candidate)
        if matches:
            chosen = max(matches, key=lambda candidate: candidate['score'])
            x1, y1, x2, y2 = chosen['roi']
            h = y2-y1
            block['roi'] = (0., max(0., y1-h*.4), 1., min(1., y2+h*.4))
            block['caption_candidate'] = chosen['roi']
        elif before_gap <= 25 and after_gap <= 25:
            # Sparse probes can miss a short caption even between stable bands.
            # OCR still has to find a real caption-shaped box in each frame.
            block['roi'] = typical_band
            block['caption_candidate'] = typical_box
            block['anchor_fallback'] = True
        else:
            continue
        block['ambiguous'] = False
        block['anchored'] = True
    return blocks


def matches_caption_region(box, band, reference):
    """Keep caption-shaped text, excluding smaller editor labels and status bars."""
    x1, y1, x2, y2 = box.box
    band_y1, band_y2 = band[1], band[3]
    full_center_x = band[0] + (x1+x2)/2 * (band[2]-band[0])
    full_center_y = band_y1 + (y1+y2)/2 * (band_y2-band_y1)
    full_height = (y2-y1) * (band_y2-band_y1)
    rx1, ry1, rx2, ry2 = reference
    return (full_height >= max(.01, (ry2-ry1)*.7)
            and abs(full_center_y-(ry1+ry2)/2) <= max(.015, (ry2-ry1)*.35)
            and abs(full_center_x-(rx1+rx2)/2) <= .25)


def caption_references(block):
    """Include both subtitle lines while excluding nearby editor text tracks."""
    candidates = block.get('candidates') or []
    primary = block.get('caption_candidate') or (candidates[0]['roi'] if candidates else None)
    if primary is None:
        return []
    result = [primary]
    if not block.get('roi'):
        return result
    ph = primary[3]-primary[1]
    pw = primary[2]-primary[0]
    px = (primary[0]+primary[2])/2
    py = (primary[1]+primary[3])/2
    primary_track = next((c for c in candidates if c['roi'] == primary), None)
    primary_score = primary_track['score'] if primary_track else (candidates[0]['score'] if candidates else 0.)
    primary_times = set(primary_track.get('times', [])) if primary_track else set()
    for candidate in candidates:
        roi = candidate['roi']
        if roi == primary or candidate['score'] < max(.28, primary_score*.8):
            continue
        h = roi[3]-roi[1]
        w = roi[2]-roi[0]
        x = (roi[0]+roi[2])/2
        y = (roi[1]+roi[3])/2
        distance = abs(y-py)
        if (ph > 0 and pw > 0 and .7 <= h/ph <= 1.6 and .7 <= w/pw <= 1.4
                and abs(x-px) <= .2 and ph*.75 <= distance <= ph*2
                and len(primary_times.intersection(candidate.get('times', []))) >= 2
                and block['roi'][1] <= y <= block['roi'][3]):
            result.append(roi)
    return result
