import tempfile
import unittest
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.gpt.universal_gpt import UniversalGPT
from app.gpt.timestamp_markers import resolve_timestamp_markers
from app.models.gpt_model import GPTSource
from app.models.transcriber_model import TranscriptSegment
from app.utils.note_helper import replace_content_markers
from app.utils.screenshot_marker import extract_screenshot_timestamps


def response(markdown):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=markdown))])


class SourceTimestampTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Avoid creating runtime storage or calling a model provider.
        self.gpt = UniversalGPT.__new__(UniversalGPT)
        self.gpt.client = SimpleNamespace(base_url='https://example.invalid')
        self.gpt.model = 'test'
        self.gpt.temperature = .7
        self.gpt.max_request_bytes = 100_000
        self.gpt.checkpoint_dir = Path(self.temp.name)
        self.gpt.screenshot = False
        self.gpt.link = False
        self.source = GPTSource(
            title='长视频 OCR', tags=[], _format=['link', 'screenshot'],
            segment=[
                TranscriptSegment(42.2, 43.0, '开场'),
                TranscriptSegment(3599.8, 3600.2, '小时边界前'),
                TranscriptSegment(3600.2, 3601.0, '小时边界后'),
                TranscriptSegment(4240.8, 4241.0, '健康'),
                TranscriptSegment(10228.6, 10229.0, '认知'),
                TranscriptSegment(15748.0, 15749.0, '结束'),
            ],
        )

    def test_hours_are_resolved_from_source_and_used_by_links_and_screenshots(self):
        self.gpt._chat_completion_create = Mock(return_value=response(
            '## 开场 *Content-[t=42]*\n'
            '## 边界前 *Content-[t=3599]*\n'
            '## 边界后 *Content-[t=3600]*\n'
            '## 健康 *Content-[t=4240]*\n'
            '*Screenshot-[t=10228]*\n'
            '## 结束 *Content-[t=15748]*'
        ))
        markdown = self.gpt.summarize(self.source)
        for timestamp in ['00:42', '59:59', '01:00:00', '01:10:40', '02:50:28', '04:22:28']:
            self.assertIn(f'[{timestamp}]', markdown)
        links = replace_content_markers(markdown, 'BVtest_p2')
        self.assertIn('[原片 @ 01:10:40](https://www.bilibili.com/video/BVtest?p=2&t=4240)', links)
        self.assertEqual(extract_screenshot_timestamps(markdown), [('*Screenshot-[02:50:28]*', 10228)])
        self.assertNotIn('[t=', markdown)
        prompt = self.gpt._chat_completion_create.call_args.args[0][0]['content']
        self.assertIn('01:10:40 [t=4240] - 健康', prompt)

    def test_invalid_model_timestamps_retry_once_and_never_reach_cache(self):
        for invalid in ['10:40', '24:80', '01:10:40', 't=9999', 't=4240.8', 't=-1', '']:
            with self.subTest(invalid=invalid):
                self.gpt._chat_completion_create = Mock(return_value=response(f'*Content-[{invalid}]*'))
                with self.assertRaises(ValueError):
                    self.gpt.summarize(self.source)
                self.assertEqual(self.gpt._chat_completion_create.call_count, 2)
                self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_retry_recovers_valid_source_reference(self):
        self.gpt._chat_completion_create = Mock(side_effect=[
            response('*Content-[10:40]*'), response('*Content-[t=4240]*'),
        ])
        self.assertEqual(self.gpt.summarize(self.source), '*Content-[01:10:40]*')

    def test_required_links_cannot_silently_disappear(self):
        self.gpt._chat_completion_create = Mock(return_value=response('## 健康\n没有时间'))
        with self.assertRaises(ValueError):
            self.gpt.summarize(self.source)
        self.assertEqual(self.gpt._chat_completion_create.call_count, 2)

    def test_merge_preserves_source_anchors_until_final_resolution(self):
        self.gpt._chat_completion_create = Mock(side_effect=[
            response('## 健康 *Content-[t=4240]*\n## 认知 *Content-[t=10228]*'),
        ])
        merged = self.gpt._merge_partials(
            ['## 健康 *Content-[t=4240]*', '## 认知 *Content-[t=10228]*'], None, None,
        )
        self.assertIn('[t=10228]', merged)
        self.assertIn('Content-[02:50:28]', resolve_timestamp_markers(merged, {4240, 10228}))

    def test_merge_rejects_invented_times_even_if_they_exist_elsewhere_in_video(self):
        self.gpt._chat_completion_create = Mock(return_value=response('*Content-[t=10228]*'))
        with self.assertRaises(ValueError):
            self.gpt._merge_partials(['*Content-[t=42]*', '*Content-[t=4240]*'], None, None)

    def test_checkpoint_keeps_anchors_and_resume_formats_hours(self):
        self.source.checkpoint_key = 'ocr_markdown'
        signature = self.gpt._build_source_signature(self.source)
        self.gpt._save_checkpoint('ocr_markdown', signature, ['*Content-[t=4240]*'], 'summarize')
        self.gpt._chat_completion_create = Mock(side_effect=AssertionError('should resume'))
        self.assertEqual(self.gpt.summarize(self.source), '*Content-[01:10:40]*')
        self.assertFalse(self.gpt._checkpoint_path('ocr_markdown').exists())

    def test_legacy_summary_signature_is_invalidated(self):
        with patch('app.gpt.universal_gpt.json.dumps', wraps=json.dumps) as dumps:
            signature = self.gpt._build_source_signature(self.source)
        payload = dumps.call_args.args[0]
        self.assertEqual(payload['timestamp_format'], 'source-seconds-v2')
        payload['timestamp_format'] = 'elapsed-hms-v1'
        old_signature = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.assertNotEqual(signature, old_signature)
        self.gpt._save_checkpoint('old', old_signature, ['*Content-[10:40]*'], 'summarize')
        self.assertIsNone(self.gpt._load_checkpoint('old', signature))

    def test_multiple_chunks_merge_then_resolve_hours(self):
        # Leave room for exactly one segment per request.
        self.source.segment = self.source.segment[-2:]
        self.source.segment[0].text *= 500
        self.source.segment[1].text *= 500
        one_segment = self.gpt.create_messages(
            [self.source.segment[0]], title=self.source.title,
            tags=self.source.tags, _format=self.source._format,
        )
        self.gpt.max_request_bytes = self.gpt._estimate_messages_bytes(one_segment) + 100
        self.gpt._chat_completion_create = Mock(side_effect=[
            response('## 认知 *Content-[t=10228]*'),
            response('## 结束 *Content-[t=15748]*'),
            response('## 认知 *Content-[t=10228]*\n## 结束 *Content-[t=15748]*'),
        ])
        markdown = self.gpt.summarize(self.source)
        self.assertEqual(self.gpt._chat_completion_create.call_count, 3)
        self.assertIn('*Content-[02:50:28]*', markdown)
        self.assertIn('*Content-[04:22:28]*', markdown)
        self.assertNotIn('[t=', markdown)

    def test_notes_without_time_formats_still_work(self):
        self.source._format = []
        self.gpt._chat_completion_create = Mock(return_value=response('## 总结\n内容'))
        self.assertEqual(self.gpt.summarize(self.source), '## 总结\n内容')

    def test_truncated_or_unbracketed_markers_are_rejected(self):
        for markdown in ['*Content-[t=4240', '*Content-t=4240*', '*Screenshot-', '*Content-[]*']:
            with self.subTest(markdown=markdown), self.assertRaises(ValueError):
                resolve_timestamp_markers(markdown, {4240})

    def test_ordinary_http_headers_are_not_timestamp_markers(self):
        markdown = '```http\nContent-Type: application/json\nContent-Length: 4240\n```'
        self.assertEqual(resolve_timestamp_markers(markdown, {4240}), markdown)


if __name__ == '__main__':
    unittest.main()
