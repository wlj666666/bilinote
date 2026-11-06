"""Evaluation accounting must not hide failures or confuse tokens / evidence."""
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/evaluate_agent.py'
spec = importlib.util.spec_from_file_location('agent_eval', SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
report_spec = importlib.util.spec_from_file_location('agent_eval_report', SCRIPT.with_name('report_agent_evaluation.py'))
reporting = importlib.util.module_from_spec(report_spec)
with patch.dict(sys.modules, {'evaluate_agent': evaluation}):
    report_spec.loader.exec_module(reporting)


class AgentEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.case = {'id': 'case', 'video': 'v', 'answer_groups': [['策略'], ['工厂']],
                     'evidence_groups': [['策略模式加工厂模式']], 'time_seconds': [688.5]}

    def record(self, answer='策略模式和工厂模式，11:28。', **kwargs):
        row = {'case_id': 'case', 'repeat': 0, 'mode': 'agent', 'category': 'timestamp',
               'answer': answer, 'elapsed_seconds': 2, 'trace': [
                   {'kind': 'retrieval', 'chunks': [{'text': '初始参考不包含原话'}]},
                   {'kind': 'tool', 'result': json.dumps({'text': '策略模式加工厂模式'}, ensure_ascii=False),
                    'success': True},
                   {'kind': 'completion', 'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}]}
        row.update(kwargs)
        row['score'] = evaluation.score(self.case, row)
        return row

    def test_all_facts_and_actual_timestamp_are_required(self):
        self.assertTrue(self.record()['score']['passed'])
        self.assertFalse(self.record('策略模式，11:28')['score']['passed'])
        self.assertFalse(self.record('策略模式和工厂模式，10:28')['score']['passed'])
        # The number alone is not a source timestamp.
        self.assertFalse(self.record('策略模式和工厂模式，688')['score']['passed'])

    def test_error_empty_and_truncated_answers_cannot_pass(self):
        self.assertFalse(self.record(error={'type': 'TimeoutError'})['score']['passed'])
        self.assertFalse(self.record(truncated=True)['score']['passed'])
        self.assertFalse(self.record('')['score']['passed'])

    def test_tool_evidence_counts_even_when_initial_retrieval_misses(self):
        row = self.record()
        self.assertTrue(row['score']['evidence_available'])
        row['trace'] = [row['trace'][0]]
        self.assertFalse(evaluation.score(self.case, row)['evidence_available'])

    def test_refusal_cannot_hide_an_invented_numeric_claim(self):
        case = {'answer_groups': [['未提供']], 'forbidden_patterns': [r'p99为\d+']}
        self.assertTrue(evaluation.score(case, {'answer': '未提供P99。'})['passed'])
        self.assertFalse(evaluation.score(case, {'answer': '未提供，不过P99为30毫秒。'})['passed'])

    def test_timestamp_units_and_hours(self):
        values = evaluation.timestamps('01:10:40 / 25:27 / 1527秒 / 688.5s / 3分46秒 / 90%')
        for expected in [4240, 1527, 688.5, 226]:
            self.assertIn(expected, values)
        self.assertNotIn(90, values)

    def test_failed_requests_stay_in_denominator_and_usage_is_not_duplicated(self):
        success = self.record()
        failure = self.record(error={'type': 'TimeoutError'})
        failure['case_id'] = 'failed'
        failure['trace'] = [{'kind': 'completion', 'error_type': 'TimeoutError'}]
        summary = evaluation.summarize([success, failure])['modes']['agent']
        self.assertEqual((summary['total'], summary['passed'], summary['pass_rate']), (2, 1, .5))
        self.assertEqual(summary['total_tokens'], 120)
        self.assertEqual(summary['usage_missing_requests'], 1)
        self.assertEqual(summary['errors'], 1)

    def test_paired_wins_only_compare_matching_cases_and_repeats(self):
        agent = self.record()
        rag = self.record(mode='rag', answer='不知道')
        unpaired = self.record(repeat=1)
        paired = evaluation.summarize([agent, rag, unpaired])['paired']['agent']
        self.assertEqual(paired, {'pairs': 1, 'agent_only_pass': 1, 'rag_only_pass': 0})

    def test_corpus_snapshot_strips_headers_and_refuses_input_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            note = {'markdown': '## 笔记\n策略模式加工厂模式',
                    'transcript': {'segments': [{'start': 0, 'end': 1, 'text': '策略模式加工厂模式'}]},
                    'audio_meta': {'title': '公开视频', 'raw_info': {'uploader': '作者',
                                   'http_headers': {'Authorization': 'PRIVATE'}, 'formats': ['PRIVATE']}}}
            path = source / 'v.json'
            path.write_text(json.dumps(note), encoding='utf-8')
            dataset = {'videos': [{'id': 'v', 'task_id': 'task', 'source_note': 'v.json'}], 'cases': [self.case]}
            evaluation.prepare_corpus(dataset, source, root / 'output')
            snapshot = (root / 'output/notes/task.json').read_text(encoding='utf-8')
            self.assertNotIn('PRIVATE', snapshot)
            note['markdown'] += '\n已修改'
            path.write_text(json.dumps(note), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Frozen corpus changed'):
                evaluation.prepare_corpus(dataset, source, root / 'output')

    def test_reference_evidence_must_exist_and_paths_cannot_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = {'videos': [{'id': 'v', 'task_id': 'task', 'source_note': '../private.json'}], 'cases': []}
            with self.assertRaisesRegex(ValueError, 'escapes root'):
                evaluation.prepare_corpus(dataset, root, root / 'output')

    def test_dataset_rejects_duplicate_ids_and_empty_rubrics(self):
        dataset = {'videos': [{'id': 'v'}], 'cases': [self.case, dict(self.case)]}
        with self.assertRaises(ValueError):
            evaluation.validate_dataset(dataset)
        dataset['cases'] = [dict(self.case, answer_groups=[[]])]
        with self.assertRaises(ValueError):
            evaluation.validate_dataset(dataset)

    def test_report_rejects_missing_pairs_or_changed_generation_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = []
            for mode in ['rag', 'agent_original', 'agent_fixed']:
                path = root / mode
                path.mkdir()
                metadata = {'model_requested': 'model', 'temperature': 0, 'max_tokens': 8192,
                            'timeout_seconds': 180, 'repeats': 1}
                (path / 'metadata.json').write_text(json.dumps(metadata))
                (path / 'corpus_manifest.json').write_text(json.dumps([{'video': 'v', 'snapshot_sha256': 'same'}]))
                row = self.record(mode='rag' if mode == 'rag' else 'agent', question='问题')
                (path / 'results.jsonl').write_text(json.dumps(row) + '\n')
                runs.append((mode, path, row['mode']))
            dataset = {'version': 'test', 'videos': [{'id': 'v'}],
                       'cases': [dict(self.case, question='问题')]}
            result, rows = reporting.build_report(dataset, runs)
            self.assertEqual(len(rows), 3)
            self.assertTrue(result['identical_initial_retrieval'])
            metadata['temperature'] = .7
            (runs[-1][1] / 'metadata.json').write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, 'generation settings differ'):
                reporting.build_report(dataset, runs)
            (runs[-1][1] / 'results.jsonl').write_text('')
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                reporting.build_report(dataset, runs)
if __name__ == '__main__':
    unittest.main()
