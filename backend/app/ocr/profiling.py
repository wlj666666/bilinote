"""Opt-in diagnostics for the existing RapidOCR path; never enabled by default.

ORT node durations describe host-side node events, not CUDA kernel occupancy or
device-only time. Do not sum concurrent workers' node times as elapsed wall time.
"""
from collections import Counter, defaultdict
from functools import wraps
import json
from pathlib import Path
import time


def build_profiled_rapidocr(params, profile_dir):
    """Inject ORT sessions before model initialization, preserving RapidOCR config.

    RapidOCR 3.9.2 supports a custom InferenceSession in Det/Cls/Rec.session.
    The small _initialize adapter uses that hook without editing site-packages.
    """
    from omegaconf import flag_override
    import onnxruntime as ort
    from rapidocr import RapidOCR
    from rapidocr.inference_engine.onnxruntime.main import OrtInferSession
    from rapidocr.inference_engine.onnxruntime.provider_config import ProviderConfig

    directory = Path(profile_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)

    class ProfiledRapidOCR(RapidOCR):
        def _initialize(self, cfg):
            engine_cfg = cfg.EngineConfig.onnxruntime
            providers = ProviderConfig(engine_cfg).get_ep_list()
            root = Path(cfg.Global.model_root_dir)
            models = {
                'Det': 'PP-OCRv6_det_small.onnx',
                'Cls': 'ch_ppocr_mobile_v2.0_cls_mobile.onnx',
                'Rec': 'PP-OCRv6_rec_small.onnx',
            }
            with flag_override(cfg, 'allow_objects', True):
                for section, filename in models.items():
                    options = OrtInferSession._init_sess_opts(engine_cfg)
                    options.enable_profiling = True
                    options.profile_file_prefix = str(directory / section.lower())
                    cfg[section].session = ort.InferenceSession(
                        str(root / filename), sess_options=options, providers=providers)
                super()._initialize(cfg)

    return ProfiledRapidOCR(params=params)


def summarize_profile(path):
    events = json.loads(Path(path).read_text(encoding='utf-8'))
    providers = Counter()
    operators = defaultdict(lambda: {'events': 0, 'host_node_ms': 0.})
    shapes = Counter()
    for event in events:
        args = event.get('args') or {}
        provider = args.get('provider')
        if event.get('cat') != 'Node' or not provider:
            continue
        providers[provider] += 1
        key = (provider, args.get('op_name', 'unknown'))
        operators[key]['events'] += 1
        operators[key]['host_node_ms'] += float(event.get('dur', 0)) / 1000
        if args.get('input_type_shape') is not None:
            shapes[json.dumps(args['input_type_shape'], sort_keys=True)] += 1
    ordered = sorted(({'provider': p, 'op_name': op, **v}
                      for (p, op), v in operators.items()),
                     key=lambda row: row['host_node_ms'], reverse=True)
    return {'path': str(path), 'provider_events': dict(providers),
            'host_node_ms': sum(row['host_node_ms'] for row in ordered),
            'operators': ordered, 'distinct_input_shapes': len(shapes),
            'timing_scope': 'ORT host node events, NOT CUDA kernel-only time'}


def finish_profiles(engine):
    results = {}
    for name in ('text_det', 'text_cls', 'text_rec'):
        session = getattr(engine.engine, name).session.session
        path = session.end_profiling()
        if path:
            results[name] = summarize_profile(path)
    return results


def force_detection_limit(engine, side):
    """Diagnostic ablation for RapidOCR 3.9.2's max-mode dynamic size tiers.

    Its TextDetector.get_preprocess ignores configured limit_side_len in max
    mode and selects 960/1500/2000 by image size. Explicitly test an effective
    cap here without silently changing production detection behavior.
    """
    from rapidocr.ch_ppocr_det.utils import DetPreProcess
    detector = engine.engine.text_det
    detector.get_preprocess = lambda _max_wh: DetPreProcess(
        side, detector.limit_type, detector.mean, detector.std)


class StageRecorder:
    """Measure nested business stages and native session.run wall times.

    One recorder per process/engine. RapidOCR mutates per-call flags and is not
    shared between threads by these experiments.
    """
    def __init__(self, engine):
        self.rows = []
        self.frame_id = None
        raw = engine.engine
        for name in ('preprocess_img', 'detect_and_crop', 'cls_and_rotate',
                     'recognize_txt', 'build_final_output'):
            setattr(raw, name, self._wrap(name, getattr(raw, name)))
        for name in ('text_det', 'text_cls', 'text_rec'):
            session = getattr(raw, name).session.session
            session.run = self._wrap(name + '.run', session.run)

    def _wrap(self, name, fn):
        @wraps(fn)
        def measured(*args, **kwargs):
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                if self.frame_id is not None:
                    row = {'frame_id': self.frame_id, 'stage': name, 'start': start,
                           'seconds': time.perf_counter() - start}
                    if name.endswith('.run') and len(args) > 1 and isinstance(args[1], dict):
                        row['input_shapes'] = {key: list(value.shape) for key, value in args[1].items()
                                               if hasattr(value, 'shape')}
                    self.rows.append(row)
        return measured

    def summary(self):
        totals = defaultdict(lambda: {'calls': 0, 'seconds': 0.})
        for row in self.rows:
            totals[row['stage']]['calls'] += 1
            totals[row['stage']]['seconds'] += row['seconds']
        return dict(totals)
