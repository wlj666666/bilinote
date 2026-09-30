import json
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

from app.ocr.ocr_engine import TextBox
from app.ocr.subtitle_tracker import locate, merge_observations
from app.services.hard_subtitle_extractor import HardSubtitleExtractor


def box(text, y=.35, x=.3, width=.4, score=.98):
    return TextBox((x, y, x+width, y+.035), text, score)


def test_dynamic_subtitle_beats_static_document_and_watermark():
    captions = ['先说一下本人的情况', '本人本硕都是中上985', '二八届毕业', '有过端到端的实习']
    samples = [(i, [box(text), box('Python 自动驾驶提问正文一直不变', .65), box('程序员YT', .03)])
               for i, text in enumerate(captions*3)]
    roi, candidates, ambiguous = locate(samples)
    assert roi and roi[1] < .35 < roi[3] < .5
    assert not ambiguous


def test_width_changes_follow_same_center():
    words = ['你好世界', '今天讲自动驾驶的就业方向', '这是一句话', '字幕长度不断发生变化']
    samples = [(i, [box(t, x=.5-(.15+i*.08)/2, width=.15+i*.08)]) for i,t in enumerate(words)]
    roi, _, ambiguous = locate(samples)
    assert roi and not ambiguous


def test_static_text_and_moving_comments_are_not_subtitles():
    assert locate([(i, [box('固定标题')]) for i in range(10)])[0] is None
    samples = [(i, [box(f'不同的弹幕{i}', x=.05+i*.08, width=.1)]) for i in range(10)]
    assert locate(samples)[0] is None


def test_competing_dynamic_regions_are_uncertain():
    a = ['今天聊自动驾驶', '软件工程师岗位', '编程基础很重要', '介绍一些学习路线']
    b = ['这里是另一段文字', '正文也在持续变化', '无法确定哪个字幕', '应该进入不确定状态']
    roi, _, ambiguous = locate([(i, [box(a[i%4], .2), box(b[i%4], .8)]) for i in range(12)])
    assert roi and ambiguous


def test_caption_repetition_gaps_numbers_and_negations_survive():
    obs = [(0., '可以买', .9), (.2, '可以买', .95), (.4, '不可以买', .95),
           (.6, '可以买', .9), (2., '可以买', .9), (2.2, '价格100元', .9), (2.4, '价格1000元', .9)]
    segments = merge_observations(obs, .2, 3.)
    assert [s.text for s in segments] == ['可以买', '不可以买', '可以买', '可以买', '价格100元', '价格1000元']
    assert segments[0].end == pytest.approx(.4)


def test_no_text_is_not_engine_failure(tmp_path):
    engine = Mock()
    engine.read.return_value = []
    extractor = HardSubtitleExtractor(engine=engine)
    frames = [(float(i), np.zeros((20,30,3), dtype=np.uint8)) for i in range(5)]
    with patch.object(extractor, 'duration', return_value=5), patch.object(extractor, 'frames', return_value=iter(frames)):
        result = extractor.extract('unused', tmp_path/'report.json')
    assert result.status == 'no_subtitles'
    engine.read.side_effect = RuntimeError('model broken')
    with patch.object(extractor, 'duration', return_value=5), patch.object(extractor, 'frames', return_value=iter(frames)):
        result = extractor.extract('unused', tmp_path/'report.json')
    assert result.status == 'failed'
    assert 'model broken' in result.message


def test_decode_truncation_cannot_be_reported_as_no_subtitles(tmp_path):
    extractor = HardSubtitleExtractor(engine=Mock(read=Mock(return_value=[])))
    with patch.object(extractor, 'duration', return_value=100), patch.object(extractor, 'frames', return_value=iter([(0., np.zeros((20,30,3)))])):
        result = extractor.extract('unused', tmp_path/'report.json')
    assert result.status == 'failed'


def test_sampling_covers_long_video_and_final_frame(tmp_path):
    # Real video decode tests run without an OCR model.
    import av
    path = tmp_path/'clip.mp4'
    with av.open(str(path), 'w') as out:
        stream = out.add_stream('mpeg4', rate=10)
        stream.width, stream.height, stream.pix_fmt = 32, 32, 'yuv420p'
        for i in range(103):
            frame = av.VideoFrame.from_ndarray(np.full((32,32,3), i, dtype=np.uint8), format='rgb24')
            for packet in stream.encode(frame): out.mux(packet)
        for packet in stream.encode(): out.mux(packet)
    frames = list(HardSubtitleExtractor.frames(path, .2))
    assert frames[0][0] == 0
    assert frames[-1][0] >= 10
    assert len(frames) > 45


def test_layout_change_is_split_in_time_not_treated_as_simultaneous_competition():
    from app.ocr.subtitle_tracker import locate_windows
    texts = ['今天介绍自动驾驶', '首先讲一下背景', '编程需要打好基础', '换一个画面继续', '字幕移动到了上方', '现在介绍学习路线']
    samples = [(i*2., [box(text, .7 if i<3 else .2)]) for i,text in enumerate(texts)]
    windows = locate_windows(samples, 0, 12)
    assert len(windows) == 2
    assert windows[0]['roi'][1] > .6 and windows[1]['roi'][1] < .3
    assert all(not w['ambiguous'] for w in windows)


def test_native_worker_stall_is_terminated_without_asr(tmp_path, monkeypatch):
    import io
    import queue
    import sys
    from types import SimpleNamespace
    from app.ocr import worker_runner
    from app.services.hard_subtitle_extractor import TranscriptResult, TranscriptSegment
    # Older downloader/GPT tests install module stubs during collection.
    monkeypatch.setitem(sys.modules, 'app.models.transcriber_model',
                        SimpleNamespace(TranscriptResult=TranscriptResult, TranscriptSegment=TranscriptSegment))
    process = Mock()
    process.stdout = io.StringIO('')
    process.poll.return_value = None
    process.wait.return_value = 0
    class EmptyQueue:
        def put(self, value): pass
        def get(self, timeout): raise queue.Empty
    monkeypatch.setattr(worker_runner.subprocess, 'Popen', Mock(return_value=process))
    monkeypatch.setattr(worker_runner.queue, 'Queue', EmptyQueue)
    clock = iter([0., 121.])
    monkeypatch.setattr(worker_runner.time, 'monotonic', lambda: next(clock))
    result = worker_runner.run_worker('video.mp4', tmp_path/'timeout.json', 5, 20)
    assert result.status == 'failed'
    assert '无进度' in result.message
    process.terminate.assert_called_once()
    assert result.transcript is None


def test_programming_operators_are_not_lost_during_deduplication():
    segments = merge_observations([(0.,'C',.99),(.2,'C++',.99),(.4,'A > B',.99),(.6,'A < B',.99)], .2, 1)
    assert [s.text for s in segments] == ['C','C++','A > B','A < B']


def test_crop_edge_shapes_are_rejected_without_dropping_real_single_characters():
    from app.ocr.subtitle_tracker import within_caption_band
    # Coordinates measured from actual false positives in the portrait sample.
    assert not within_caption_band(TextBox((.3,.04,.4,.80),'3',.93))
    assert not within_caption_band(TextBox((.3,.057,.405,.724),'Y',.67))
    assert within_caption_band(TextBox((.4,.23,.44,.77),'3',.99))
    assert within_caption_band(TextBox((.4,.23,.44,.77),'C',.99))
    assert within_caption_band(TextBox((.1,.04,.9,.80),'We learn about OCR',.99))


@pytest.mark.parametrize('end_card', [False, True])
def test_static_closing_caption_continues_established_region(tmp_path, end_card):
    engine = Mock()
    captions = ['介绍一下本人情况', '现在说明技术背景', '接下来分析问题', '最后给出建议']
    probes = [(float(i), np.zeros((100,100,3), dtype=np.uint8)) for i in range(0,30,2)]
    reads = [[] if end_card and i>=24 else [box(captions[(i//2)%4] if i<20 else '感谢观看')] for i in range(0,30,2)]
    recognition = [(i*.2, np.zeros((100,100,3), dtype=np.uint8)) for i in range(150)]
    engine.read.side_effect = reads + [[] if end_card and ts>=24 else [TextBox((.3, .22, .7, .78), '字幕内容' if ts<20 else '感谢观看', .98)] for ts,_ in recognition]
    extractor = HardSubtitleExtractor(engine=engine)
    with patch.object(extractor, 'duration', return_value=30.), patch.object(extractor, 'frames', side_effect=[iter(probes),iter(recognition)]):
        result = extractor.extract('unused',tmp_path/'report.json')
    # A six-second blank exceeds the quality gate for this short synthetic clip;
    # the diagnostic transcript must still preserve the final spoken caption.
    assert result.status == ('uncertain' if end_card else 'success')
    assert result.transcript.segments[-1].text == '感谢观看'
    assert result.transcript.segments[-1].end == pytest.approx(24. if end_card else 30.)


def test_stable_caption_band_recovers_editor_competition():
    from app.ocr.subtitle_tracker import stabilize_ambiguous_blocks

    def item(start, end, y, ambiguous=False, competitors=None):
        candidate = {'score': .65, 'roi': (.3, y, .7, y+.055),
                     'examples': ['讲话字幕', '下一句字幕'], 'times': [start, end]}
        return {'start': start, 'end': end,
                'roi': (0., y-.02, 1., y+.075), 'ambiguous': ambiguous,
                'candidates': competitors if competitors is not None else [candidate]}

    code = {'score': .67, 'roi': (.2, .35, .8, .38),
            'examples': ['import module', 'export default'], 'times': [60, 62]}
    caption = {'score': .65, 'roi': (.3, .89, .7, .945),
               'examples': ['这里是真字幕', '下一句真字幕'], 'times': [60, 62]}
    blocks = [item(i*20, (i+1)*20, .89) for i in range(3)]
    blocks += [item(60, 80, .35, True, [code, caption])]
    blocks += [item(i*20, (i+1)*20, .89) for i in range(4, 7)]
    stabilize_ambiguous_blocks(blocks)
    assert blocks[3]['anchored']
    assert blocks[3]['roi'][1] > .85
    assert blocks[3]['caption_candidate'] == caption['roi']


def test_short_missing_probe_uses_stable_band_but_filters_editor_text():
    from app.ocr.subtitle_tracker import matches_caption_region, stabilize_ambiguous_blocks

    def stable(start):
        return {'start': start, 'end': start+20, 'roi': (0., .86, 1., .97),
                'ambiguous': False, 'candidates': [
                    {'score': .7, 'roi': (.3, .89, .7, .945), 'examples': [], 'times': []}]}

    blocks = [stable(i*20) for i in range(3)]
    blocks.append({'start': 60, 'end': 65, 'roi': None, 'ambiguous': True,
                   'candidates': [{'score': .7, 'roi': (.2, .35, .8, .38), 'examples': [], 'times': []}]})
    blocks += [stable(65+i*20) for i in range(3)]
    stabilize_ambiguous_blocks(blocks)
    rescued = blocks[3]
    assert rescued['anchor_fallback']
    band, reference = rescued['roi'], rescued['caption_candidate']
    assert matches_caption_region(TextBox((.436, .245, .562, .811), '就是一行', .99), band, reference)
    assert not matches_caption_region(TextBox((.357, 0, .487, .208), '<h1 class', .99), band, reference)
    assert not matches_caption_region(TextBox((.661, .377, .98, .792), 'Ln 65, Col 89', .93), band, reference)
    assert not matches_caption_region(TextBox((.4, .45, .65, .759), 'libo (25 minutes ago)', .97), band, reference)


def test_competing_global_caption_bands_remain_uncertain():
    from app.ocr.subtitle_tracker import stabilize_ambiguous_blocks

    def stable(start, y):
        return {'start': start, 'end': start+20, 'roi': (0., y-.02, 1., y+.075),
                'ambiguous': False, 'candidates': [
                    {'score': .7, 'roi': (.3, y, .7, y+.055), 'examples': [], 'times': []}]}

    blocks = [stable(i*20, .2 if i%2 else .89) for i in range(10)]
    uncertain = {'start': 200, 'end': 205, 'roi': None, 'ambiguous': True,
                 'candidates': [{'score': .6, 'roi': (.3, .89, .7, .945), 'examples': [], 'times': []}]}
    blocks.append(uncertain)
    stabilize_ambiguous_blocks(blocks)
    assert uncertain['ambiguous']
    assert uncertain['roi'] is None


def test_false_reliable_status_bar_uses_neighboring_caption_profile():
    from app.ocr.subtitle_tracker import matches_caption_region, stabilize_ambiguous_blocks

    def caption(start):
        return {'start': start, 'end': start+20, 'roi': (0., .86, 1., .97),
                'ambiguous': False, 'candidates': [
                    {'score': .7, 'roi': (.3, .89, .7, .945), 'examples': [], 'times': []}]}

    blocks = [caption(i*20) for i in range(3)]
    weak = {'start': 60, 'end': 80, 'roi': (0., .87, 1., .97), 'ambiguous': False,
            'candidates': [{'score': .65, 'roi': (.7, .91, .9, .936), 'examples': [], 'times': []}]}
    blocks.append(weak)
    blocks += [caption(i*20) for i in range(4, 7)]
    stabilize_ambiguous_blocks(blocks)
    assert weak['profile_override']
    assert weak['caption_candidate'] == (.3, .89, .7, .945)
    assert not matches_caption_region(
        TextBox((.7, .4, .9, .7), 'Ln 65, Col 13 Spaces: 2', .99),
        weak['roi'], weak['caption_candidate'])


def test_two_line_captions_keep_both_lines_after_geometry_filter(tmp_path):
    engine = Mock()
    probes = [(float(i), np.zeros((100, 100, 3), dtype=np.uint8)) for i in range(0, 10, 2)]
    recognition = [(i*.2, np.zeros((100, 100, 3), dtype=np.uint8)) for i in range(50)]
    probe_reads = [[TextBox((.3, .3, .7, .335), f'上行{i}', .99),
                    TextBox((.3, .36, .7, .395), f'下行{i}', .99)] for i in range(0, 10, 2)]
    crop_reads = [[TextBox((.3, .1, .7, .4), '上行', .99),
                   TextBox((.3, .6, .7, .9), '下行', .99)] for _ in recognition]
    engine.read.side_effect = probe_reads + crop_reads
    extractor = HardSubtitleExtractor(engine=engine)
    with patch.object(extractor, 'duration', return_value=10.), patch.object(extractor, 'frames', side_effect=[iter(probes), iter(recognition)]):
        result = extractor.extract('unused', tmp_path/'two-lines.json')
    assert result.status == 'success'
    assert any('上行' in segment.text and '下行' in segment.text for segment in result.transcript.segments)


def test_caption_references_exclude_same_line_menus_and_narrow_editor_rows():
    from app.ocr.subtitle_tracker import caption_references

    times = [0., 2., 4., 6.]
    primary = {'score': .9, 'roi': (.3, .89, .7, .945), 'times': times}
    menu = {'score': .9, 'roi': (.4, .89, .6, .945), 'times': times}
    narrow_row = {'score': .9, 'roi': (.45, .835, .55, .88), 'times': times}
    second_line = {'score': .9, 'roi': (.31, .825, .69, .875), 'times': times}
    block = {'roi': (0., .8, 1., .97), 'candidates': [primary, menu, narrow_row, second_line]}
    assert caption_references(block) == [primary['roi'], second_line['roi']]
