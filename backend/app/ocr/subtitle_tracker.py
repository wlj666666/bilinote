"""Temporal subtitle localization and conservative adjacent-caption merging."""
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
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
