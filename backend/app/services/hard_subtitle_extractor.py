"""Whole-video, local OCR subtitle extraction with task-local diagnostics."""
from dataclasses import asdict, dataclass
import json
import math
from bisect import bisect_right
import os
from pathlib import Path

from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.ocr.ocr_engine import OCREngine
from app.ocr.subtitle_tracker import locate_windows, merge_observations, within_caption_band

PIPELINE_VERSION = 2


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
    def frames(path, step, max_width=None):
        import av
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ValueError('文件没有视频画面，无法提取硬字幕')
            stream = container.streams.video[0]
            stream.thread_count = 1
            origin = float((stream.start_time or 0) * stream.time_base)
            next_ts = 0.
            for frame in container.decode(stream):
                if frame.time is None:
                    raise ValueError('视频帧缺少时间戳，无法建立字幕时间轴')
                ts = max(0., float(frame.time)-origin)
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
            samples = [[] for _ in range(math.ceil(duration/self.block_seconds))]
            last = -1.
            # Samples span the WHOLE video. A negative early probe never terminates scanning.
            for ts, img in self.frames(video_path, 2., max_width=1280):
                last = ts
                boxes = self.engine.read(img)
                samples[min(int(ts/self.block_seconds), len(samples)-1)].append((ts, boxes))
                progress('detect', min(.99, ts/duration))
            if last < 0 or duration-last > 3.5:
                raise ValueError('视频扫描提前结束，不能把缺失画面当作无字幕')
            report['detection_scan_complete'] = True
            previous = None
            for index, block in enumerate(samples):
                windows = locate_windows(block, index*self.block_seconds,
                                         min(duration, (index+1)*self.block_seconds))
                # Closing captions often remain static: corroborate the preceding
                # region across most final probes, instead of requiring text changes.
                if not windows[0]['roi'] and previous and index == len(samples)-1:
                    matched = [any(previous[1] <= (b.box[1]+b.box[3])/2 <= previous[3] and b.score >= .8
                                   for b in boxes) for _, boxes in block]
                    # The first probe can continue the established band even
                    # when the very next probe is already an end card.
                    leading = bool(matched and matched[0])
                    if sum(matched) >= max(1, math.ceil(len(block)*.6)) or leading:
                        windows[0]['roi'] = previous
                report['blocks'].extend(windows)
                previous = windows[-1]['roi']
            atomic_json(diagnostic_path, {**report, "status": "extracting"})
            usable = [b for b in report['blocks'] if b['roi'] and not b['ambiguous']]
            if not usable:
                uncertain = any(b['ambiguous'] or (b['candidates'] and b['candidates'][0]['score'] > .1)
                                for b in report['blocks'])
                return self._finish('uncertain' if uncertain else 'no_subtitles', None, report, diagnostic_path,
                    '无法可靠定位讲话字幕，请重试或改用语音转写' if uncertain else '未检测到可提取的动态画面字幕，可改用语音转写')
            starts = [b["start"] for b in report["blocks"]]
            observations = []
            low = total = edge_shapes = 0
            last = -1.
            step = 1/self.fps
            # Stream frames; only text and small diagnostics are retained in memory.
            for ts, img in self.frames(video_path, step):
                last = ts
                block = report['blocks'][max(0, bisect_right(starts, ts)-1)]
                roi = block['roi']
                if roi and not block['ambiguous']:
                    h, w = img.shape[:2]
                    x1,y1,x2,y2 = roi
                    crop = img[int(y1*h):max(int(y1*h)+1, int(y2*h)), int(x1*w):max(int(x1*w)+1,int(x2*w))]
                    boxes = self.engine.read(crop)
                    edge_shapes += sum(not within_caption_band(b) for b in boxes)
                    boxes = [b for b in boxes if within_caption_band(b)]
                    boxes.sort(key=lambda b: (round(b.box[1]*5), b.box[0]))
                    if boxes:
                        total += 1
                        good = [b for b in boxes if b.score >= .65]
                        if len(good) != len(boxes):
                            low += 1
                        if good:
                            observations.append((ts, ' '.join(b.text for b in good), min(b.score for b in good)))
                progress('extract', min(.99, ts/duration))
            if last < 0 or duration-last > max(1., step*3):
                raise ValueError('视频识别提前结束，请重试')
            segments = merge_observations(observations, step, duration)
            missing = sum(b['end']-b['start'] for b in report['blocks'] if not b['roi'] or b['ambiguous'])
            gaps = []
            cursor = 0.
            for segment in segments:
                if segment.start-cursor >= 2.:
                    gaps.append([cursor, segment.start])
                cursor = segment.end
            if duration-cursor >= 2.:
                gaps.append([cursor, duration])
            gap_seconds = sum(end-start for start,end in gaps)
            report.update(scan_complete=True, text_gaps=gaps, segment_count=len(segments),
                          rejected_edge_shapes=edge_shapes,
                          missing_region_seconds=missing, low_score_fraction=low/max(total, 1))
            transcript = TranscriptResult(language='zh' if any('\u4e00' <= c <= '\u9fff' for seg in segments for c in seg.text) else 'en', full_text=' '.join(s.text for s in segments),
                segments=segments, raw={'source': 'hard_subtitle_ocr', 'engine': 'rapidocr',
                    'pipeline_version': PIPELINE_VERSION, 'fps': self.fps, 'quality_status': 'passed', 'unlocated_seconds': missing, 'text_gap_seconds': gap_seconds,
                    'runtime': report.get('runtime')})
            if not segments or missing > max(20., duration*.15) or gap_seconds > max(3., duration*.15) or low/max(total,1) > .15:
                transcript.raw['quality_status'] = 'uncertain'
                return self._finish('uncertain', transcript, report, diagnostic_path,
                    '部分字幕区域或文字识别不稳定，已保留诊断结果；请重试或改用语音转写')
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
