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
    engine.read.side_effect = reads + [[] if end_card and ts>=24 else [box('字幕内容' if ts<20 else '感谢观看')] for ts,_ in recognition]
    extractor = HardSubtitleExtractor(engine=engine)
    with patch.object(extractor, 'duration', return_value=30.), patch.object(extractor, 'frames', side_effect=[iter(probes),iter(recognition)]):
        result = extractor.extract('unused',tmp_path/'report.json')
    # A six-second blank exceeds the quality gate for this short synthetic clip;
    # the diagnostic transcript must still preserve the final spoken caption.
    assert result.status == ('uncertain' if end_card else 'success')
    assert result.transcript.segments[-1].text == '感谢观看'
    assert result.transcript.segments[-1].end == pytest.approx(24. if end_card else 30.)
