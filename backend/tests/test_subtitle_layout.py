"""Caption position, switching and gap review behavior, independent of OCR models."""
from unittest.mock import Mock, patch

import numpy as np
import pytest

from app.models.transcriber_model import TranscriptSegment
from app.ocr.ocr_engine import OCREngine, TextBox
from app.ocr.subtitle_layout import learn_layouts, resolve_layouts, matches_layout, select_caption_boxes
from app.ocr.subtitle_quality import classify_gaps, plan_review, quality_summary
from app.services.hard_subtitle_extractor import HardSubtitleExtractor


def caption(text, y=.6, width=.4, score=.99):
    return TextBox((.5-width/2, y, .5+width/2, y+.04), text, score)


def timeline(samples, duration):
    layouts, candidates = learn_layouts(samples)
    return layouts, resolve_layouts(samples, duration, layouts, candidates)


@pytest.mark.parametrize('y', [.15, .5, .85])
def test_long_pause_does_not_switch_to_dynamic_menus(y):
    samples = []
    for i in range(80):
        boxes = [TextBox((.02, .01, .16, .025), f'菜单{i%4}', .99)]
        if i < 20 or i >= 60:
            boxes.append(caption(f'讲话字幕{i}', y, width=.1+(i%5)*.1))
        samples.append((i*2., boxes))
    layouts, blocks = timeline(samples, 160.)
    assert len({block['layout_id'] for block in blocks}) == 1
    assert all(block['roi'][1] < y < block['roi'][3] for block in blocks)
    assert all(not block['ambiguous'] for block in blocks)
    # A short, static one-character caption follows an established model.
    assert matches_layout(caption('不', y, .025).box, (0, 0, 1, 1), layouts[blocks[0]['layout_id']])


def test_sustained_exclusive_layout_change_is_confirmed_at_its_onset():
    samples = [(i*2., [caption(f'字幕{i}', .7 if i<10 else .2)]) for i in range(20)]
    layouts, blocks = timeline(samples, 40.)
    assert len({block['layout_id'] for block in blocks}) == 2
    assert blocks[0]['roi'][1] > .65
    assert blocks[-1]['roi'][3] < .3
    assert next(block['start'] for block in blocks if block['roi'][3] < .3) == 19.


def test_single_probe_at_another_position_cannot_trigger_switch():
    samples = [(i*2., [caption(f'字幕{i}', .2 if i==10 else .7)]) for i in range(20)]
    _, blocks = timeline(samples, 40.)
    assert all(block['roi'][1] > .65 for block in blocks)


def test_two_probe_short_video_can_establish_a_layout():
    layouts, blocks = timeline([(0.,[caption('第一句字幕')]),(2.,[caption('换了一句话')])],4.)
    assert layouts and blocks
    assert blocks[0]['start'] == 0. and blocks[-1]['end'] == 4.


def test_comparable_simultaneous_regions_are_not_silently_selected():
    samples = [(i*2., [caption(f'第一行{i}', .2), caption(f'第二处{i}', .8)]) for i in range(12)]
    _, blocks = timeline(samples, 24.)
    assert all(block['ambiguous'] for block in blocks)


def test_tall_watermark_recognition_jitter_does_not_compete_with_captions():
    samples = [(i*2., [caption(f'讲话字幕{i}',.3),
                       TextBox((.65,.4,.85,.5),'固定水印YT' if i%2 else '固定水印Y1',.95)]) for i in range(30)]
    layouts, blocks = timeline(samples,60.)
    assert all(not block['ambiguous'] and block['roi'][3] < .4 for block in blocks)


def test_two_lines_form_one_layout_despite_different_caption_widths():
    samples = [(i*2., [caption(f'上行{i}', .6, .2+i*.04), caption(f'下行{i}', .66, .5-i*.03)]) for i in range(8)]
    layouts, blocks = timeline(samples, 16.)
    assert len(layouts[blocks[0]['layout_id']]['lines']) == 2
    assert all(not block['ambiguous'] for block in blocks)


def test_separately_detected_words_are_aligned_as_a_caption_line():
    line = dict(height=.04, baseline=.64, alignment='center', anchor=.5)
    layout = dict(lines=[line])
    fragments = [TextBox((.2,.6,.48,.64),'我是很难跟大家讲出',.99),
                 TextBox((.5,.6,.78,.64),'一个',.99)]
    label = TextBox((.9,.6,.98,.64),'状态栏',.99)
    assert not any(matches_layout(box.box,(0,0,1,1),layout) for box in fragments)
    assert select_caption_boxes(fragments+[label],(0,0,1,1),layout,1.6) == fragments


def test_gap_is_not_verified_by_empty_recognition_alone():
    segments = [TranscriptSegment(0, 2, '开头'), TranscriptSegment(8, 10, '结尾')]
    evidence = {i: dict(time=i, state='blank') for i in range(2, 8)}
    gaps = classify_gaps(segments, 10, evidence, 1.)
    assert gaps[0]['kind'] == 'insufficient_evidence'
    assert quality_summary(gaps, evidence, 1., 10)['quality_reasons']
    for frame in evidence.values():
        frame['state'] = 'verified_blank'
    gaps = classify_gaps(segments, 10, evidence, 1.)
    assert gaps[0]['kind'] == 'no_subtitles'
    assert not quality_summary(gaps, evidence, 1., 10)['quality_reasons']
    evidence[4]['state'] = 'detected_unreadable'
    assert classify_gaps(segments, 10, evidence, 1.)[0]['kind'] == 'recognition_failed'


@pytest.mark.parametrize('unreadable', [False, True])
def test_pipeline_distinguishes_a_real_end_card_from_unreadable_subtitles(tmp_path, unreadable):
    class Engine:
        def read(self, image):
            ts = float(image[0,0,0])
            if ts >= 24.:
                return []
            if image.shape[0] == 100:
                return [TextBox((.3,.35,.7,.385), f'介绍字幕{int(ts)}', .99)]
            return [TextBox((.3,.22,.7,.78), '最后字幕', .99)] if image.shape[0] <= 7 else [TextBox((.3,.34,.7,.66), '最后字幕', .99)]
        def detect(self, image):
            return [TextBox((.3,.34,.7,.66),'',.95)] if unreadable else []
    extractor = HardSubtitleExtractor(engine=Engine(),fps=5)
    def frames(path, step, max_width=None, start=0., end=None):
        for i in range(int(round(start/step)), int(round((end or 30.)/step))):
            ts = round(i*step,6)
            yield ts, np.full((100,100,3),ts,dtype=np.float32)
    with patch.object(extractor,'duration',return_value=30.), patch.object(extractor,'frames',side_effect=frames):
        result = extractor.extract('unused',tmp_path/'report.json')
    assert result.status == ('uncertain' if unreadable else 'success')
    kind = 'recognition_failed' if unreadable else 'no_subtitles'
    assert result.diagnostics['classified_gaps'][0]['kind'] == kind
    assert result.transcript.segments[-1].end == pytest.approx(24.)
    if unreadable:
        assert '低置信度文字比例' in result.message


def test_sparse_negative_checks_cannot_clear_a_long_gap():
    evidence = {0: dict(time=3., state='verified_blank')}
    gaps = classify_gaps([], 10., evidence, .2)
    assert gaps[0]['kind'] == 'insufficient_evidence'


def test_review_budget_is_bounded_and_unresolved_long_gap_still_fails():
    gaps = [dict(start=i*10., end=i*10.+9, kind='layout_uncertain') for i in range(10)]
    intervals = plan_review(gaps, 100., .2)
    assert sum(end-start for start,end in intervals) <= 30.
    long_gap = [dict(start=20., end=80., kind='insufficient_evidence')]
    assert quality_summary(long_gap, {}, .2, 1000.)['quality_reasons'] == ['continuous_unresolved_gap']


def test_review_replaces_wrong_observations_and_keeps_detector_failures(tmp_path):
    layout = dict(id=0, roi=(0., .584, 1., .656), lines=[
        dict(height=.04, baseline=.64, anchor=.5, alignment='center', geometry=(.3,.6,.7,.64))])
    blocks = [dict(start=0., end=4., layout_id=0, roi=layout['roi'], ambiguous=False)]
    class Engine:
        def read(self, image):
            # Full frames show no competing caption; expanded crop is unreadable.
            return []
        def detect(self, image):
            return [TextBox((.3, .34, .7, .66), '', .9)]
    extractor = HardSubtitleExtractor(engine=Engine(), fps=5)
    observations = {0:(0., '文件 编辑', .99)}
    evidence = {}
    frames = [(i*.2, np.zeros((100,100,3),dtype=np.uint8)) for i in range(20)]
    with patch.object(extractor, 'frames', return_value=iter(frames)):
        extractor._review('unused', [(0.,4.)], blocks, [layout], observations, evidence, .2, lambda *args:None)
    assert not observations
    assert all(frame['state'] == 'detected_unreadable' for frame in evidence.values())


def test_review_preserves_trusted_context_when_a_different_crop_is_blank():
    layout = dict(id=0,roi=(0.,.58,1.,.66),lines=[
        dict(height=.04,baseline=.64,anchor=.5,alignment='center',geometry=(.3,.6,.7,.64))])
    block = dict(start=0.,end=1.,layout_id=0,roi=layout['roi'],ambiguous=False)
    class Engine:
        def read(self,image): return []
        def detect(self,image): return []
    extractor = HardSubtitleExtractor(engine=Engine())
    observations = {0:(0.,'已有可靠字幕',.99)}
    evidence = {0:dict(time=0.,state='text')}
    frames = [(i*.2,np.zeros((100,100,3),dtype=np.uint8)) for i in range(2)]
    with patch.object(extractor,'frames',return_value=iter(frames)):
        extractor._review('unused',[(0.,.4)],[block],[layout],observations,evidence,.2,lambda *args:None)
    assert observations[0][1] == '已有可靠字幕'
    assert evidence[0]['state'] == 'text'
    assert evidence[1]['state'] == 'verified_blank'


def test_detector_does_not_disable_later_recognition():
    # RapidOCR flags persist, so the adapter must explicitly restore them.
    engine = OCREngine.__new__(OCREngine)
    engine.engine = Mock(return_value=Mock(boxes=None, txts=None))
    image = np.zeros((20,40,3),dtype=np.uint8)
    engine.detect(image)
    engine.read(image)
    assert engine.engine.call_args.kwargs['use_rec'] is True
    assert engine.engine.call_args.kwargs['use_cls'] is True


def test_frame_seek_preserves_global_timestamps_and_sampling_grid(tmp_path):
    import av
    path = tmp_path/'seek.mp4'
    with av.open(str(path),'w') as container:
        stream = container.add_stream('mpeg4',rate=10)
        stream.width = stream.height = 32
        stream.pix_fmt = 'yuv420p'
        for i in range(100):
            frame = av.VideoFrame.from_ndarray(np.full((32,32,3),i,dtype=np.uint8),format='rgb24')
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
    frames = list(HardSubtitleExtractor.frames(path,.2,start=4.1,end=5.1))
    assert [round(ts,1) for ts,_ in frames] == [4.2,4.4,4.6,4.8,5.0]
