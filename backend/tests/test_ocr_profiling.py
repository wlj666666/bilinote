import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from app.ocr.profiling import StageRecorder, summarize_profile, force_detection_limit


class OCRProfilingTests(unittest.TestCase):
    def test_profile_counts_only_executed_node_events_with_provider(self):
        events = [
            {'cat': 'Session', 'dur': 900000, 'args': {}},
            {'cat': 'Node', 'name': 'conv_fence_before', 'dur': 7, 'args': {}},
            {'cat': 'Node', 'dur': 1200, 'args': {'provider': 'CUDAExecutionProvider', 'op_name': 'Conv'}},
            {'cat': 'Node', 'dur': 800, 'args': {'provider': 'CUDAExecutionProvider', 'op_name': 'Conv'}},
            {'cat': 'Node', 'dur': 100, 'args': {'provider': 'CPUExecutionProvider', 'op_name': 'Shape'}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'trace.json'
            path.write_text(json.dumps(events))
            report = summarize_profile(path)
        self.assertEqual(report['provider_events'], {'CUDAExecutionProvider': 2, 'CPUExecutionProvider': 1})
        self.assertAlmostEqual(report['host_node_ms'], 2.1)
        self.assertEqual(report['operators'][0], {
            'provider': 'CUDAExecutionProvider', 'op_name': 'Conv', 'events': 2, 'host_node_ms': 2.})
        self.assertIn('NOT CUDA', report['timing_scope'])

    def test_no_inference_events_does_not_fabricate_gpu_use(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'trace.json'
            path.write_text('[{"cat":"Session", "dur":1000}]')
            report = summarize_profile(path)
        self.assertEqual(report['provider_events'], {})
        self.assertEqual(report['host_node_ms'], 0)

    def test_stage_recorder_excludes_warmup_and_records_failures(self):
        def identity(value):
            return value

        raw = SimpleNamespace(**{name: identity for name in (
            'preprocess_img', 'detect_and_crop', 'cls_and_rotate', 'recognize_txt', 'build_final_output')})
        for name in ('text_det', 'text_cls', 'text_rec'):
            setattr(raw, name, SimpleNamespace(session=SimpleNamespace(session=SimpleNamespace(run=identity))))
        recorder = StageRecorder(SimpleNamespace(engine=raw))
        raw.preprocess_img('warmup')
        self.assertEqual(recorder.rows, [])
        recorder.frame_id = 42
        self.assertEqual(raw.preprocess_img('measured'), 'measured')
        self.assertEqual(raw.text_det.session.session.run('input'), 'input')

        def fail():
            raise RuntimeError('inference failure')

        with self.assertRaisesRegex(RuntimeError, 'inference failure'):
            recorder._wrap('failing_stage', fail)()
        self.assertEqual(recorder.summary()['failing_stage']['calls'], 1)
        self.assertEqual({row['frame_id'] for row in recorder.rows}, {42})

    def test_explicit_detection_cap_changes_actual_preprocessed_tensor_size(self):
        import numpy as np
        detector = SimpleNamespace(limit_type='max', mean=[.5]*3, std=[.5]*3)
        engine = SimpleNamespace(engine=SimpleNamespace(text_det=detector))
        force_detection_limit(engine, 960)
        preprocessor = detector.get_preprocess(1920)
        tensor = preprocessor(np.zeros((1080, 1920, 3), dtype=np.uint8))
        self.assertEqual(tensor.shape, (1, 3, 544, 960))


if __name__ == '__main__':
    unittest.main()
