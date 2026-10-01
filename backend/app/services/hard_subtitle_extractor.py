"""Whole-video, local OCR subtitle extraction with task-local diagnostics."""
from dataclasses import asdict, dataclass
import json
import math
from bisect import bisect_right
import os
from pathlib import Path
import time

from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.ocr.ocr_engine import OCREngine
from app.ocr.subtitle_tracker import merge_observations, within_caption_band
from app.ocr.subtitle_layout import learn_layouts, resolve_layouts, select_caption_boxes
from app.ocr.subtitle_quality import classify_gaps, plan_review, quality_summary

PIPELINE_VERSION = 4


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


@dataclass
class ExtractionResult:
    status: str
    transcript: TranscriptResult | None
    diagnostics: dict
    message: str = ''


class HardSubtitleExtractor:
    def __init__(self, engine=None, fps=None, block_seconds=20):
        self.engine = engine
        self.fps = float(fps or os.getenv('OCR_FPS', '5'))
        if not 1 <= self.fps <= 12:
            raise ValueError('OCR_FPS 必须在 1 到 12 之间')
        self.block_seconds = block_seconds

    @staticmethod
    def frames(path, step, max_width=None, start=0., end=None):
        import av
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ValueError('文件没有视频画面，无法提取硬字幕')
            stream = container.streams.video[0]
            stream.thread_count = 1
            origin = float((stream.start_time or 0) * stream.time_base)
            if start:
                container.seek(int((start+origin)/stream.time_base), stream=stream, backward=True)
            next_ts = math.ceil((start-1e-6)/step)*step
            for frame in container.decode(stream):
                if frame.time is None:
                    raise ValueError('视频帧缺少时间戳，无法建立字幕时间轴')
                ts = max(0., float(frame.time)-origin)
                if end is not None and ts >= end-1e-6:
                    break
                if ts + 1e-6 < next_ts:
                    continue
                next_ts = (int(ts/step + 1e-6)+1)*step
                if max_width and frame.width > max_width:
                    frame = frame.reformat(width=max_width, height=round(frame.height*max_width/frame.width))
                yield ts, frame.to_ndarray(format='rgb24')

    @staticmethod
    def duration(path):
        import av
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ValueError('文件没有视频画面，无法提取硬字幕')
            stream = container.streams.video[0]
            duration = float(stream.duration * stream.time_base) if stream.duration else float(container.duration or 0)/av.time_base
            if duration <= 0:
                raise ValueError('无法读取视频时长')
            return duration

    @staticmethod
    def _pixel_roi(image, roi):
        height, width = image.shape[:2]
        x1, y1, x2, y2 = roi
        return (int(x1*width)/width, int(y1*height)/height,
                max(int(x1*width)+1, int(x2*width))/width,
                max(int(y1*height)+1, int(y2*height))/height)

    @staticmethod
    def _crop(image, roi):
        height, width = image.shape[:2]
        x1, y1, x2, y2 = roi
        return image[int(y1*height):max(int(y1*height)+1, int(y2*height)),
                     int(x1*width):max(int(x1*width)+1, int(x2*width))]

    def _read_band(self, image, roi, layout):
        roi = self._pixel_roi(image, roi)
        boxes = self.engine.read(self._crop(image, roi))
        edge = sum(not within_caption_band(box) for box in boxes)
        boxes = [box for box in boxes if within_caption_band(box)]
        selected = select_caption_boxes(boxes, roi, layout, image.shape[1]/image.shape[0])
        rejected = len(boxes)-len(selected)
        boxes = selected
        good = [box for box in boxes if box.score >= .65]
        text = ' '.join(box.text for box in good)
        state = 'low_score' if len(good) != len(boxes) else 'text' if good else 'blank'
        return text, min((box.score for box in good), default=0.), state, edge, rejected

    def _review(self, video_path, intervals, blocks, layouts, observations, evidence, step, progress):
        """One bounded pass; preserve negative detector evidence and replace old text."""
        starts = [block['start'] for block in blocks]
        work = sum(end-start for start, end in intervals)
        reviewed = []
        done = 0.
        detector = getattr(type(self.engine), 'detect', None)
        for start, end in intervals:
            first = last = None
            replacements = recovered = checked_blank = 0
            full_checks = []
            pending_blanks = []
            for ts, image in self.frames(video_path, step, start=start, end=end):
                first = ts if first is None else first
                last = ts
                slot = int(round(ts/step))
                block = blocks[max(0, bisect_right(starts, ts)-1)]
                layout = layouts[block['layout_id']]
                # Context frames outside a gap already have trustworthy text.
                # A different crop must not erase it merely because OCR differs.
                if slot in observations and evidence.get(slot, {}).get('state') == 'text':
                    progress('review', min(.99, (done+ts-start)/max(work, step)))
                    continue
                # A genuinely competing layout must remain uncertain, even when
                # one of its lines has a high recognition score.
                if block['ambiguous']:
                    progress('review', min(.99, (done+ts-start)/max(work, step)))
                    continue
                roi = block['roi']
                band_height = roi[3]-roi[1]
                expanded = (0., max(0., roi[1]-band_height*.35), 1., min(1., roi[3]+band_height*.35))
                detected = None
                if detector is not None:
                    detected = self.engine.detect(self._crop(image, expanded))
                    detected = select_caption_boxes(detected, self._pixel_roi(image, expanded), layout,
                                                    image.shape[1]/image.shape[0])
                if detected == []:
                    text, score, state = '', 0., 'blank'
                else:
                    text, score, state, _, _ = self._read_band(image, expanded, layout)
                    if state == 'blank' and detected:
                        state = 'detected_unreadable'
                previous = observations.pop(slot, None)
                replacements += 1
                if text:
                    observations[slot] = (ts, text, score)
                    recovered += previous is None
                evidence[slot] = dict(time=ts, state=state, reviewed=True)
                # Check the entire frame regularly for possible relocation.
                if not full_checks or ts-full_checks[-1]['time'] >= 1.-1e-6:
                    full_boxes = self.engine.detect(image) if detector is not None else self.engine.read(image)
                    suspicious = any(box.score >= .5 and any(
                        .72 <= (box.box[3]-box.box[1])/line['height'] <= 1.55
                        and abs((box.box[0]+box.box[2])/2-(line['geometry'][0]+line['geometry'][2])/2) <= .055
                        and abs(box.box[3]-line['baseline']) > line['height']*.65
                        for line in layout['lines']) for box in full_boxes)
                    full_checks.append(dict(time=ts, possible_relocation=suspicious))
                if state == 'blank':
                    # Only actual detection results are allowed to confirm a blank.
                    # Test/custom engines without detect() leave it unverified.
                    if detected == []:
                        pending_blanks.append((slot, ts))
                progress('review', min(.99, (done+ts-start)/max(work, step)))
            if first is None or first-start > step*2 or end-(last+step) > step*2:
                raise ValueError('字幕局部复查解码不完整，请重试')
            for slot, ts in pending_blanks:
                nearby = [check for check in full_checks if abs(check['time']-ts) <= 1.+step]
                if nearby and not any(check['possible_relocation'] for check in nearby):
                    evidence[slot]['state'] = 'verified_blank'
                    checked_blank += 1
            reviewed.append(dict(start=start, end=end, replaced_frames=replacements,
                                 recovered_frames=recovered, verified_blank_frames=checked_blank,
                                 full_frame_checks=full_checks))
            done += end-start
        return reviewed

    def extract(self, video_path, diagnostic_path, progress=None):
        if self.engine is not None:
            return self._extract_direct(video_path, diagnostic_path, progress)
        # Native codec/OCR failures cannot leave the main task worker blocked forever.
        # spawn a clean interpreter rather than forking a process with native threads.
        from app.ocr.worker_runner import run_worker
        return run_worker(video_path, diagnostic_path, self.fps, self.block_seconds, progress)

    def _extract_direct(self, video_path, diagnostic_path, progress=None):
        report = {'pipeline_version': PIPELINE_VERSION, 'fps': self.fps, 'blocks': []}
        progress = progress or (lambda phase, fraction: None)
        try:
            self.engine = self.engine or OCREngine()
            runtime_info = getattr(self.engine, 'runtime_info', None)
            if isinstance(runtime_info, dict):
                report['runtime'] = runtime_info
            duration = self.duration(video_path)
            report['duration'] = duration
            timings = {}
            started = time.monotonic()
            samples = []
            last = -1.
            for ts, image in self.frames(video_path, 2., max_width=1280):
                last = ts
                samples.append((ts, self.engine.read(image)))
                progress('detect', min(.99, ts/duration))
            if last < 0 or duration-last > 3.5:
                raise ValueError('视频扫描提前结束，不能把缺失画面当作无字幕')
            report['detection_scan_complete'] = True
            layouts, candidates = learn_layouts(samples, self.block_seconds, progress)
            blocks = resolve_layouts(samples, duration, layouts, candidates, self.block_seconds)
            report.update(layouts=layouts, blocks=blocks)
            timings['detect_seconds'] = time.monotonic()-started
            if not blocks:
                weak = (any(candidate['score'] > .1 for tracks in candidates.values() for candidate in tracks)
                        or any(0. < box.score < .65 for _, boxes in samples for box in boxes))
                return self._finish('uncertain' if weak else 'no_subtitles', None, report, diagnostic_path,
                    '无法建立可靠字幕位置模型，请重试或改用语音转写' if weak else '未检测到可提取的动态画面字幕，可改用语音转写')
            atomic_json(diagnostic_path, {**report, 'status': 'extracting'})
            starts = [block['start'] for block in blocks]
            observations = {}
            evidence = {}
            edge_shapes = non_caption_shapes = 0
            last = -1.
            step = 1/self.fps
            started = time.monotonic()
            for ts, image in self.frames(video_path, step):
                last = ts
                slot = int(round(ts/step))
                block = blocks[max(0, bisect_right(starts, ts)-1)]
                if block['ambiguous']:
                    evidence[slot] = dict(time=ts, state='ambiguous')
                else:
                    layout = layouts[block['layout_id']]
                    text, score, state, edge, rejected = self._read_band(image, block['roi'], layout)
                    edge_shapes += edge
                    non_caption_shapes += rejected
                    evidence[slot] = dict(time=ts, state=state)
                    if text:
                        observations[slot] = (ts, text, score)
                progress('extract', min(.99, ts/duration))
            if last < 0 or duration-last > max(1., step*3):
                raise ValueError('视频识别提前结束，请重试')
            timings['extract_seconds'] = time.monotonic()-started
            segments = merge_observations(sorted(observations.values()), step, duration)
            initial_gaps = classify_gaps(segments, duration, evidence, step)
            intervals = plan_review(initial_gaps, duration, step)
            report['initial_gaps'] = initial_gaps
            started = time.monotonic()
            if intervals:
                progress('review', 0.)
                report['reviews'] = self._review(video_path, intervals, blocks, layouts,
                                                observations, evidence, step, progress)
                segments = merge_observations(sorted(observations.values()), step, duration)
            else:
                report['reviews'] = []
            timings['review_seconds'] = time.monotonic()-started
            gaps = classify_gaps(segments, duration, evidence, step)
            quality = quality_summary(gaps, evidence, step, duration)
            gap_seconds = sum(gap['end']-gap['start'] for gap in gaps)
            report.update(scan_complete=True, text_gaps=[[gap['start'], gap['end']] for gap in gaps],
                          classified_gaps=gaps, segment_count=len(segments),
                          rejected_edge_shapes=edge_shapes, rejected_non_caption_shapes=non_caption_shapes,
                          frame_evidence=list(evidence.values()), timings=timings, **quality)
            transcript = TranscriptResult(
                language='zh' if any('\u4e00' <= c <= '\u9fff' for seg in segments for c in seg.text) else 'en',
                full_text=' '.join(segment.text for segment in segments), segments=segments,
                raw={'source': 'hard_subtitle_ocr', 'engine': 'rapidocr', 'pipeline_version': PIPELINE_VERSION,
                     'fps': self.fps, 'quality_status': 'passed', 'unlocated_seconds': quality['missing_region_seconds'],
                     'text_gap_seconds': gap_seconds, 'gap_seconds_by_kind': quality['gap_seconds_by_kind'],
                     'unresolved_gap_seconds': quality['unresolved_gap_seconds'],
                     'verified_no_subtitle_seconds': quality['verified_no_subtitle_seconds'],
                     'quality_reasons': quality['quality_reasons'], 'runtime': report.get('runtime')})
            if not segments or quality['quality_reasons']:
                transcript.raw['quality_status'] = 'uncertain'
                details = []
                if quality['unresolved_gap_seconds']:
                    details.append(f"仍有 {quality['unresolved_gap_seconds']:.1f} 秒空缺未解决")
                if quality['missing_region_seconds']:
                    details.append(f"{quality['missing_region_seconds']:.1f} 秒位置不确定")
                if 'recognition_unstable' in quality['quality_reasons']:
                    details.append(f"低置信度文字比例 {quality['low_score_fraction']:.1%}")
                if not segments:
                    details.append('未提取到可靠文字')
                return self._finish('uncertain', transcript, report, diagnostic_path,
                    '字幕质量复查未通过：'+'；'.join(details)+'；已保留诊断结果，可重试或改用语音转写')
            return self._finish('success', transcript, report, diagnostic_path)
        except Exception as exc:
            return self._finish('failed', None, report, diagnostic_path, f'OCR 提取失败：{exc}')

    @staticmethod
    def _finish(status, transcript, report, path, message=''):
        report.update(status=status, message=message)
        if transcript:
            # Keep partial text for inspection even when it must not reach the summarizer.
            report['transcript'] = asdict(transcript)
        atomic_json(path, report)
        return ExtractionResult(status, transcript, report, message)
