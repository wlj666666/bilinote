from dataclasses import asdict
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.models.audio_model import AudioDownloadResult
from app.services import note as module
from app.services.hard_subtitle_extractor import ExtractionResult


def transcript(source=None):
    return TranscriptResult('zh', '测试字幕', [TranscriptSegment(0., 2., '测试字幕')], {'source': source} if source else {})


@pytest.fixture
def generator(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'NOTE_OUTPUT_DIR', tmp_path)
    with patch.object(module.NoteGenerator, '_init_transcriber', side_effect=AssertionError('Whisper must stay unloaded')):
        gen = module.NoteGenerator()
    gen._update_status = Mock()
    gen._save_metadata = Mock()
    gen._get_gpt = Mock(return_value=Mock())
    gen._summarize_text = Mock(return_value='生成的笔记')
    gen._get_downloader = Mock(return_value=Mock(download_subtitles=Mock(return_value=None)))
    gen._transcribe_audio = Mock(return_value=transcript('asr'))
    gen.video_path = tmp_path/'video.mp4'
    gen._download_media = Mock(return_value=AudioDownloadResult('audio.mp3', '测试', 2., None, 'bilibili', 'BVtest', {}))
    return gen


def run(gen, method):
    return gen.generate('https://www.bilibili.com/video/BVtest?p=2', 'bilibili', task_id='test', text_extraction_method=method)


def test_ocr_success_uses_same_summary_contract_without_asr(generator):
    result = ExtractionResult('success', transcript('hard_subtitle_ocr'), {})
    with patch.object(module, 'HardSubtitleExtractor') as engine:
        engine.return_value.extract.return_value = result
        output = run(generator, 'ocr')
    assert output and output.transcript.raw['source'] == 'hard_subtitle_ocr'
    generator._transcribe_audio.assert_not_called()
    assert generator._download_media.call_args.kwargs['skip_download'] is True
    assert generator._download_media.call_args.kwargs['require_video'] is True
    assert generator._summarize_text.call_args.kwargs['transcript'] is output.transcript


@pytest.mark.parametrize('status', ['no_subtitles', 'uncertain', 'failed'])
def test_ocr_failure_never_falls_back_or_summarizes(generator, status):
    with patch.object(module, 'HardSubtitleExtractor') as engine:
        engine.return_value.extract.return_value = ExtractionResult(status, None, {}, '字幕无法提取')
        assert run(generator, 'ocr') is None
    generator._transcribe_audio.assert_not_called()
    generator._summarize_text.assert_not_called()
    assert generator._update_status.call_args.args[1] == module.TaskStatus.FAILED


def test_platform_subtitle_skips_both_extractors(generator):
    generator._get_downloader.return_value.download_subtitles.return_value = transcript()
    with patch.object(module, 'HardSubtitleExtractor') as engine:
        assert run(generator, 'ocr') is not None
        engine.assert_not_called()
    generator._transcribe_audio.assert_not_called()


def test_qwen_text_model_skips_visual_upload_for_old_saved_requests(generator):
    generator._get_downloader.return_value.download_subtitles.return_value = transcript()
    result = generator.generate('https://www.bilibili.com/video/BVtest', 'bilibili',
                                task_id='test', model_name='qwen-plus',
                                video_understanding=True, text_extraction_method='ocr')
    assert result is not None
    assert generator._download_media.call_args.kwargs['video_understanding'] is False
    assert generator._download_media.call_args.kwargs['skip_download'] is True
    assert '已跳过画面理解' in generator.summary_notice
    generator._transcribe_audio.assert_not_called()


def test_vision_model_keeps_requested_visual_summary(generator):
    generator._get_downloader.return_value.download_subtitles.return_value = transcript()
    result = generator.generate('https://www.bilibili.com/video/BVtest', 'bilibili',
                                task_id='test', model_name='qwen-vl-plus', video_understanding=True)
    assert result is not None
    assert generator._download_media.call_args.kwargs['video_understanding'] is True


def test_asr_route_keeps_existing_transcriber(generator):
    with patch.object(module, 'HardSubtitleExtractor') as engine:
        assert run(generator, 'asr').transcript.raw['source'] == 'asr'
        engine.assert_not_called()
    generator._transcribe_audio.assert_called_once()


def test_switching_route_does_not_reuse_old_asr_text(generator):
    run(generator, 'asr')
    generator._transcribe_audio.reset_mock()
    with patch.object(module, 'HardSubtitleExtractor') as engine:
        engine.return_value.extract.return_value = ExtractionResult('success', transcript('hard_subtitle_ocr'), {})
        assert run(generator, 'ocr').transcript.raw['source'] == 'hard_subtitle_ocr'
        engine.return_value.extract.assert_called_once()
    generator._transcribe_audio.assert_not_called()


def test_switching_ocr_device_invalidates_text_cache(generator, monkeypatch):
    with patch.object(module, 'HardSubtitleExtractor') as engine:
        engine.return_value.extract.return_value = ExtractionResult('success', transcript('hard_subtitle_ocr'), {})
        monkeypatch.setenv('OCR_DEVICE', 'cpu')
        assert run(generator, 'ocr') is not None
        engine.return_value.extract.reset_mock()
        monkeypatch.setenv('OCR_DEVICE', 'cuda')
        assert run(generator, 'ocr') is not None
        engine.return_value.extract.assert_called_once()


def test_cache_requires_identity_and_preserves_source(tmp_path):
    path = tmp_path/'text.json'
    data = asdict(transcript('hard_subtitle_ocr'))
    data['raw'].update(cache_signature='a', input_key='video1')
    path.write_text(json.dumps(data))
    assert module.NoteGenerator._load_text_cache(path, 'b', 'video1') is None
    assert module.NoteGenerator._load_text_cache(path, 'a', 'video1').raw['source'] == 'hard_subtitle_ocr'
    data['raw'] = {'source': 'platform_subtitle', 'input_key': 'video1'}
    path.write_text(json.dumps(data))
    assert module.NoteGenerator._load_text_cache(path, 'anything', 'video2') is None
    assert module.NoteGenerator._load_text_cache(path, 'anything', 'video1') is not None


def test_metadata_only_does_not_download_audio(tmp_path):
    class Downloader:
        def download(self, video_url, quality, output_dir, need_video, skip_download=False):
            assert skip_download
            return AudioDownloadResult('', 'title', 3., None, 'bilibili', 'BVtest', {})
        def download_video(self, video_url, output_dir):
            raise AssertionError('Platform subtitles should not require video')
    gen = module.NoteGenerator()
    gen._update_status = Mock()
    result = gen._download_media(Downloader(), 'url', 'medium', tmp_path/'t_audio.json',
        module.TaskStatus.DOWNLOADING, 'bilibili', str(tmp_path), False, False, 6, [],
        skip_download=True, input_key='video1')
    assert result.file_path == ''
    assert not gen.video_img_urls


def test_ocr_requires_video_but_not_visual_summary(tmp_path):
    video = tmp_path/'video.mp4'
    video.touch()
    class Downloader:
        def download(self, video_url, quality, output_dir, need_video, skip_download=False):
            assert skip_download and need_video
            return AudioDownloadResult('', 'title', 3., None, 'bilibili', 'BVtest', {})
        def download_video(self, video_url, output_dir):
            return str(video)
    gen = module.NoteGenerator()
    gen._update_status = Mock()
    with patch.object(module, 'VideoReader') as reader:
        result = gen._download_media(Downloader(), 'url', 'medium', tmp_path/'t_audio.json',
            module.TaskStatus.DOWNLOADING, 'bilibili', str(tmp_path), False, False, 6, [],
            skip_download=True, require_video=True, input_key='video1')
        reader.assert_not_called()
    assert result.file_path == '' and gen.video_path == video


def test_request_defaults_and_failure_status_are_machine_readable(tmp_path):
    from app.routers import note as router
    request = dict(video_url='https://www.bilibili.com/video/BVtest', platform='bilibili',
        quality='medium', model_name='test', provider_id='test')
    assert router.VideoRequest(**request).text_extraction_method == 'asr'
    with pytest.raises(ValueError):
        router.VideoRequest(**request, text_extraction_method='auto')
    (tmp_path/'failed.status.json').write_text(json.dumps({'status':'FAILED','message':'OCR 无字幕'}))
    with patch.object(router, 'NOTE_OUTPUT_DIR', str(tmp_path)):
        result = router.get_task_status('failed')
    result = json.loads(result.body)
    assert result['data']['status'] == 'FAILED'
    assert result['data']['message'] == 'OCR 无字幕'


def test_asr_accepts_whisper_dataclass_metadata(generator):
    from dataclasses import dataclass
    @dataclass
    class WhisperInfo:
        language: str = 'zh'
        duration: float = 2.
    value = transcript()
    value.raw = WhisperInfo()
    generator._transcribe_audio.return_value = value
    output = run(generator, 'asr')
    assert output is not None
    assert output.transcript.raw['source'] == 'asr'
    assert output.transcript.raw['duration'] == 2.
