import tempfile
import unittest
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.gpt.universal_gpt import UniversalGPT
from app.gpt.timestamp_markers import (
    TIMESTAMP_WARNING, normalize_timestamp_markers, resolve_timestamp_markers, source_seconds,
)
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

    def test_invalid_timestamps_without_any_note_body_still_fail(self):
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
        calls = self.gpt._chat_completion_create.call_args_list
        original_messages, repair_messages = calls[0].args[0], calls[1].args[0]
        self.assertEqual(len(original_messages), 1)
        self.assertEqual(repair_messages[-2], {'role': 'assistant', 'content': '*Content-[10:40]*'})
        self.assertIn('Content-[10:40]', repair_messages[-1]['content'])
        self.assertIn('[t=4240]', repair_messages[-1]['content'])

    def test_missing_required_links_keep_body_and_show_notice(self):
        self.gpt._chat_completion_create = Mock(return_value=response('## 健康\n没有时间'))
        markdown = self.gpt.summarize(self.source)
        self.assertIn('## 健康\n没有时间', markdown)
        self.assertIn(TIMESTAMP_WARNING, markdown)
        self.assertEqual(self.gpt._chat_completion_create.call_count, 2)

    def test_real_failure_time_is_corrected_inside_subtitle_interval_without_retry(self):
        self.source.segment = [
            TranscriptSegment(1333.4, 1335.0, '忽视和必须妥协的对象'),
            TranscriptSegment(1335.2, 1337.2, '因为此时经济危机尚未爆发'),
        ]
        self.gpt._chat_completion_create = Mock(return_value=response('## 章节 *Content-[t=1334]*\n正文'))
        markdown = self.gpt.summarize(self.source)
        self.assertEqual(markdown, '## 章节 *Content-[22:13]*\n正文')
        self.gpt._chat_completion_create.assert_called_once()
        self.assertNotIn(TIMESTAMP_WARNING, markdown)

    def test_interval_correction_preserves_hours_for_links_and_screenshots(self):
        self.source.segment = [TranscriptSegment(10228.6, 10231.0, '认知')]
        self.gpt._chat_completion_create = Mock(return_value=response(
            '## 认知 *Content-[t=10229]*\n正文 *Screenshot-[t=10230]*'
        ))
        markdown = self.gpt.summarize(self.source)
        self.assertIn('*Content-[02:50:28]*', markdown)
        self.assertEqual(extract_screenshot_timestamps(markdown), [('*Screenshot-[02:50:28]*', 10228)])
        self.assertIn('&t=10228', replace_content_markers(markdown, 'BVtest_p2'))
        self.gpt._chat_completion_create.assert_called_once()

    def test_interval_correction_never_crosses_gaps_or_large_distances(self):
        segments = [TranscriptSegment(1333.4, 1335.0, '甲'), TranscriptSegment(1340, 1400, '乙')]
        for seconds in [1332, 1335, 1339, 1343, 1399, 1400, 640]:
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                normalize_timestamp_markers(f'*Content-[t={seconds}]*', source_seconds(segments), segments)

    def test_ambiguous_overlapping_intervals_are_not_guessed(self):
        segments = [TranscriptSegment(42.2, 45, '甲'), TranscriptSegment(43.2, 46, '乙')]
        with self.assertRaises(ValueError):
            normalize_timestamp_markers('*Content-[t=44]*', source_seconds(segments), segments)

    def test_unfixable_markers_are_removed_but_body_and_valid_links_survive(self):
        for invalid in ['10:40', 't=9999', 't=-1', 't=4240.8', '']:
            with self.subTest(invalid=invalid[:30]):
                self.gpt._chat_completion_create = Mock(return_value=response(
                    f'## 开场 *Content-[t=42]*\n正文\n## 健康 *Content-[{invalid}]*\n'
                    '保留的论点\n*Screenshot-[t=9999]*'
                ))
                markdown = self.gpt.summarize(self.source)
                self.assertIn('## 开场 *Content-[00:42]*', markdown)
                self.assertIn('## 健康 \n保留的论点', markdown)
                self.assertNotIn('Screenshot-', markdown)
                self.assertNotIn('[t=', markdown)
                self.assertEqual(markdown.count(TIMESTAMP_WARNING), 1)
                self.assertEqual(self.gpt._chat_completion_create.call_count, 2)

    def test_oversized_time_number_can_be_removed(self):
        markdown = '正文 *Content-[t=' + '9' * 5000 + ']*'
        self.assertEqual(normalize_timestamp_markers(markdown, {42}, drop_invalid=True), '正文 ')

    def test_incomplete_markers_are_removed_without_dropping_next_line(self):
        for invalid in ['*Content-[t=4240', '*Content-t=4240*', '*Screenshot-', '*Content-[]*']:
            with self.subTest(invalid=invalid):
                self.gpt._chat_completion_create = Mock(return_value=response(f'## 健康 {invalid}\n保留正文'))
                markdown = self.gpt.summarize(self.source)
                self.assertIn('## 健康 \n保留正文', markdown)
                self.assertIn(TIMESTAMP_WARNING, markdown)
                self.assertNotIn('Content-', markdown)
                self.assertNotIn('Screenshot-', markdown)

    def test_unclosed_marker_does_not_consume_following_prose(self):
        self.assertEqual(
            normalize_timestamp_markers('## 标题 Content-[t=9999 后续正文\n下一段', {42}, drop_invalid=True),
            '## 标题  后续正文\n下一段',
        )

    def test_notice_survives_successful_repair_that_removes_one_bad_marker(self):
        self.gpt._chat_completion_create = Mock(side_effect=[
            response('## 开场 *Content-[t=42]*\n## 健康 *Content-[t=9999]*\n正文'),
            response('## 开场 *Content-[t=42]*\n## 健康\n正文'),
        ])
        markdown = self.gpt.summarize(self.source)
        self.assertIn('Content-[00:42]', markdown)
        self.assertIn('## 健康\n正文', markdown)
        self.assertIn(TIMESTAMP_WARNING, markdown)

    def test_repair_api_failure_or_empty_response_keeps_original_note(self):
        for repair in [TimeoutError('offline'), response(None), response('')]:
            with self.subTest(repair=repair):
                self.gpt._chat_completion_create = Mock(side_effect=[
                    response('## 健康 *Content-[t=9999]*\n正文'), repair,
                ])
                markdown = self.gpt.summarize(self.source)
                self.assertIn('## 健康 \n正文', markdown)
                self.assertIn(TIMESTAMP_WARNING, markdown)

    def test_initial_api_failure_or_empty_response_still_fails(self):
        for initial in [TimeoutError('offline'), response(None), response('')]:
            with self.subTest(initial=initial):
                self.gpt._chat_completion_create = Mock(side_effect=[initial])
                with self.assertRaises((TimeoutError, ValueError)):
                    self.gpt.summarize(self.source)
                self.gpt._chat_completion_create.assert_called_once()

    def test_repair_respects_request_budget(self):
        messages = self.gpt.create_messages(
            self.source.segment, title=self.source.title, tags=self.source.tags, _format=self.source._format,
        )
        self.gpt.max_request_bytes = self.gpt._estimate_messages_bytes(messages)
        self.gpt._chat_completion_create = Mock(return_value=response('## 健康 *Content-[t=9999]*\n正文'))
        markdown = self.gpt.summarize(self.source)
        self.assertIn('正文', markdown)
        self.assertIn(TIMESTAMP_WARNING, markdown)
        self.gpt._chat_completion_create.assert_called_once()

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

    def test_merge_removes_out_of_group_times_without_discarding_body(self):
        self.gpt._chat_completion_create = Mock(return_value=response(
            '## 开场 *Content-[t=42]*\n正文\n## 健康 *Content-[t=10228]*\n更多正文'
        ))
        merged = self.gpt._merge_partials(['*Content-[t=42]*', '*Content-[t=4240]*'], None, None)
        self.assertIn('Content-[t=42]', merged)
        self.assertNotIn('t=10228', merged)
        self.assertIn('更多正文', merged)
        self.assertTrue(self.gpt._timestamp_warning)

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
        self.assertEqual(payload['timestamp_format'], 'source-intervals-v3')
        for legacy in ['elapsed-hms-v1', 'source-seconds-v2']:
            payload['timestamp_format'] = legacy
            old_signature = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            self.assertNotEqual(signature, old_signature)
            self.gpt._save_checkpoint('old', old_signature, ['*Content-[10:40]*'], 'summarize')
            self.assertIsNone(self.gpt._load_checkpoint('old', signature))

    def test_warning_survives_checkpoint_resume_and_merge(self):
        self.source.checkpoint_key = 'resume-warning'
        self.source.segment = self.source.segment[-2:]
        chunks = [SimpleNamespace(segments=[seg], image_urls=[]) for seg in self.source.segment]
        with patch('app.gpt.universal_gpt.RequestChunker.chunk', return_value=chunks):
            self.gpt._chat_completion_create = Mock(side_effect=[
                response('## 认知\n第一段正文'), response('## 认知\n第一段正文'), TimeoutError('offline'),
            ])
            with self.assertRaises(TimeoutError):
                self.gpt.summarize(self.source)
            checkpoint = json.loads(self.gpt._checkpoint_path('resume-warning').read_text())
            self.assertTrue(checkpoint['timestamp_warning'])
            self.assertEqual(checkpoint['partials'], ['## 认知\n第一段正文'])
            self.gpt._chat_completion_create = Mock(side_effect=[
                response('## 结束 *Content-[t=15748]*\n第二段正文'),
                response('## 认知\n第一段正文\n## 结束 *Content-[t=15748]*\n第二段正文'),
            ])
            markdown = self.gpt.summarize(self.source)
        self.assertIn('第一段正文', markdown)
        self.assertIn('第二段正文', markdown)
        self.assertIn('Content-[04:22:28]', markdown)
        self.assertEqual(markdown.count(TIMESTAMP_WARNING), 1)
        self.assertFalse(self.gpt._checkpoint_path('resume-warning').exists())

    def test_merge_checkpoint_does_not_resummarize_completed_chunks(self):
        self.source.checkpoint_key = 'resume-merge'
        self.source.segment = self.source.segment[-3:]
        signature = self.gpt._build_source_signature(self.source)
        self.gpt._timestamp_warning = True
        self.gpt._save_checkpoint('resume-merge', signature, [
            '## 健康\n已合并的第一段和第二段正文 *Content-[t=4240]*',
            '## 结束\n第三段正文 *Content-[t=15748]*',
        ], 'merge')
        self.gpt._chat_completion_create = Mock(return_value=response(
            '## 健康\n已合并的第一段和第二段正文 *Content-[t=4240]*\n'
            '## 结束\n第三段正文 *Content-[t=15748]*'
        ))
        markdown = self.gpt.summarize(self.source)
        # The only model request must merge saved notes, not summarize again.
        self.gpt._chat_completion_create.assert_called_once()
        prompt = self.gpt._chat_completion_create.call_args.args[0][0]['content']
        self.assertIn('已合并的第一段和第二段正文', prompt)
        self.assertNotIn('视频分段（格式：开始时间 - 内容）', prompt)
        self.assertIn('Content-[01:10:40]', markdown)
        self.assertIn('Content-[04:22:28]', markdown)
        self.assertEqual(markdown.count(TIMESTAMP_WARNING), 1)

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
