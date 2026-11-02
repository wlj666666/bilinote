"""Reproducible OCR diagnostics, fixed-work concurrency trials and report export.

Examples (run from backend, using start-gpu.sh for CUDA libraries):
  python scripts/profile_ocr.py prepare VIDEO --dataset data/ocr_profile/frames --count 96
  bash start-gpu.sh scripts/profile_ocr.py run --dataset data/ocr_profile/frames \
      --output data/ocr_profile/run --levels 1,2,3 --repeats 3
  bash start-gpu.sh scripts/profile_ocr.py run --dataset data/ocr_profile/frames \
      --output data/ocr_profile/trace --levels 1,2 --repeats 1 --profile --limit 12
  python scripts/profile_ocr.py report --root data/ocr_profile --output OCR_PROFILE_REPORT.md

Each worker owns an OCR engine. Warm-up/model loading/image loading are excluded
from steady-state wall time. There is no artificial sleep to simulate inference.
This isolated benchmark intentionally bypasses the production GPU=1 gate; it
does not change production scheduling. No network, paid API or task DB is used.
"""
import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered)-1, math.ceil(len(ordered)*fraction)-1)]


def gpu_sample():
    result = subprocess.run([
        'nvidia-smi', '--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu',
        '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=4, check=True)
    name, total, used, util, temp = [v.strip() for v in result.stdout.splitlines()[0].split(',')]
    sample = {'time': time.monotonic(), 'name': name, 'memory_total_mib': int(total),
              'memory_used_mib': int(used), 'gpu_util_percent': int(util), 'temperature_c': int(temp)}
    meminfo = Path('/proc/meminfo')
    if meminfo.exists():
        for line in meminfo.read_text().splitlines():
            if line.startswith('MemAvailable:'):
                sample['host_available_mib'] = int(line.split()[1])/1024
                break
    return sample


def prepare(args):
    import av
    import numpy as np
    video = Path(args.video).resolve()
    out = Path(args.dataset).resolve()
    if (out / 'manifest.json').exists():
        raise ValueError('Dataset already exists; choose a new directory to preserve its identity')
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    frames = []
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        stream.thread_count = 1
        origin = float((stream.start_time or 0)*stream.time_base)
        duration = float(stream.duration*stream.time_base) if stream.duration else container.duration/av.time_base
        end = min(duration, args.end if args.end is not None else duration)
        if not 0 <= args.start < end or args.count < 1:
            raise ValueError('Invalid sampling range/count')
        for i in range(args.count):
            target = args.start + (end-args.start)*(i+.5)/args.count
            container.seek(int((target+origin)/stream.time_base), stream=stream, backward=True)
            for frame in container.decode(stream):
                ts = float(frame.time)-origin
                if ts+1e-5 < target:
                    continue
                image = frame.to_ndarray(format='rgb24')
                filename = f'{i:05d}.npy'
                np.save(out/filename, image, allow_pickle=False)
                frames.append({'id': i, 'path': filename, 'time': ts,
                               'shape': list(image.shape), 'sha256': digest(out/filename)})
                break
            else:
                raise ValueError(f'Unable to decode sample at {target}')
    identity = hashlib.sha256(json.dumps(frames, sort_keys=True).encode()).hexdigest()
    manifest = {'source': str(video), 'source_sha256': digest(video), 'source_duration': duration,
                'sample_start': args.start, 'sample_end': end, 'count': len(frames),
                'roi': args.roi, 'dataset_id': identity, 'frames': frames,
                'prepare_seconds': time.perf_counter()-started}
    write_json(out/'manifest.json', manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k != 'frames'}, ensure_ascii=False), flush=True)


def worker(args):
    import numpy as np
    import onnxruntime as ort
    from app.ocr.ocr_engine import OCREngine
    from app.ocr.profiling import StageRecorder, finish_profiles, force_detection_limit
    cfg = read_json(args.config)
    out = Path(args.worker_dir)
    out.mkdir(parents=True, exist_ok=True)
    os.environ['OCR_DEVICE'] = cfg['device']
    manifest = read_json(Path(cfg['dataset'])/'manifest.json')
    selected = manifest['frames'][:cfg['limit'] or None][args.worker_id::cfg['concurrency']]
    roi = manifest['roi']
    images = []
    load_started = time.perf_counter()
    for frame in selected:
        image = np.load(Path(cfg['dataset'])/frame['path'], allow_pickle=False)
        if cfg['crop'] == 'roi':
            h, w = image.shape[:2]
            x1, y1, x2, y2 = roi
            image = image[int(y1*h):int(y2*h), int(x1*w):int(x2*w)].copy()
        images.append((frame, image))
    input_load_seconds = time.perf_counter()-load_started
    options = {
        'Det.limit_side_len': cfg['det_side'],
        'EngineConfig.onnxruntime.intra_op_num_threads': cfg['threads'],
        'EngineConfig.onnxruntime.cuda_ep_cfg.use_tf32': cfg['tf32'],
        'Rec.rec_batch_num': cfg['rec_batch'],
    }
    initialized = time.perf_counter()
    engine = OCREngine(profile_dir=out/'profiles' if cfg['profile'] else None,
                       use_cls=cfg['use_cls'], params_override=options)
    if cfg.get('force_det_side'):
        force_detection_limit(engine, cfg['det_side'])
    init_seconds = time.perf_counter()-initialized
    recorder = StageRecorder(engine)
    warmed = time.perf_counter()
    # Warm-up covers multiple natural shapes and remains excluded from stage rows.
    for _, image in images[:min(3, len(images))]:
        engine.read(image)
    warmup_seconds = time.perf_counter()-warmed
    write_json(out/'ready.json', {'pid': os.getpid(), 'init_seconds': init_seconds})
    deadline = time.monotonic()+cfg['timeout']
    while not (Path(cfg['trial_dir'])/'go').exists():
        if time.monotonic() > deadline:
            raise TimeoutError('Start barrier timed out')
        time.sleep(.01)
    start = time.perf_counter()
    cpu_start = time.process_time()
    outputs = []
    for frame, image in images:
        recorder.frame_id = frame['id']
        before = time.perf_counter()
        boxes = engine.read(image)
        elapsed = time.perf_counter()-before
        texts = []
        for box in boxes:
            x1, y1, x2, y2 = box.box
            if cfg['crop'] == 'roi':
                rx1, ry1, rx2, ry2 = roi
                x1, x2 = rx1+x1*(rx2-rx1), rx1+x2*(rx2-rx1)
                y1, y2 = ry1+y1*(ry2-ry1), ry1+y2*(ry2-ry1)
            texts.append({'text': box.text, 'score': box.score, 'box': [x1, y1, x2, y2]})
        caption = [b['text'] for b in texts
                   if roi[0] <= (b['box'][0]+b['box'][2])/2 <= roi[2]
                   and roi[1] <= (b['box'][1]+b['box'][3])/2 <= roi[3]]
        outputs.append({'frame_id': frame['id'], 'time': frame['time'], 'seconds': elapsed,
                        'texts': texts, 'caption_text': '\n'.join(caption)})
    recorder.frame_id = None
    end = time.perf_counter()
    result = {'worker_id': args.worker_id, 'pid': os.getpid(), 'start': start, 'end': end,
              'wall_seconds': end-start, 'cpu_seconds': time.process_time()-cpu_start,
              'init_seconds': init_seconds, 'warmup_seconds': warmup_seconds,
              'input_load_seconds': input_load_seconds, 'runtime': engine.runtime_info,
              'ort_version': ort.__version__, 'stages': recorder.summary(),
              'stage_rows': recorder.rows, 'outputs': outputs}
    # Profiling includes warm-up nodes, intentionally separated from latency metrics.
    if cfg['profile']:
        result['profiles_include_warmup'] = True
        result['profiles'] = finish_profiles(engine)
    write_json(out/'result.json', result)


def stop_processes(processes):
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def one_trial(cfg):
    trial = Path(cfg['trial_dir'])
    trial.mkdir(parents=True, exist_ok=False)
    write_json(trial/'config.json', cfg)
    logs, processes, samples, monitor_errors = [], [], [], []
    stopped = threading.Event()
    unsafe = threading.Event()

    def monitor():
        while not stopped.is_set():
            try:
                sample = gpu_sample()
                samples.append(sample)
                if (sample['memory_used_mib']/sample['memory_total_mib'] > .91
                        or sample['temperature_c'] >= 85
                        or sample.get('host_available_mib', 99999) < 2000):
                    unsafe.set()
            except Exception as exc:
                monitor_errors.append(str(exc))
            stopped.wait(.5)

    watcher = threading.Thread(target=monitor, daemon=True)
    if cfg['device'] == 'cuda':
        gpu_sample()  # fail before spawning if GPU access/monitoring is unavailable
        watcher.start()
    started = time.monotonic()
    try:
        for i in range(cfg['concurrency']):
            directory = trial/f'worker-{i}'
            directory.mkdir()
            log = open(directory/'worker.log', 'w', encoding='utf-8')
            logs.append(log)
            env = {**os.environ, 'OMP_NUM_THREADS': str(cfg['threads']), 'OPENBLAS_NUM_THREADS': '1',
                   'MKL_NUM_THREADS': '1', 'HF_HUB_OFFLINE': '1', 'PYTHONUNBUFFERED': '1'}
            processes.append(subprocess.Popen([
                sys.executable, str(Path(__file__).resolve()), '_worker', '--config', str(trial/'config.json'),
                '--worker-id', str(i), '--worker-dir', str(directory)], stdout=log, stderr=log, env=env))
        announced = time.monotonic()
        while not all((trial/f'worker-{i}'/'ready.json').exists() for i in range(cfg['concurrency'])):
            if any(p.poll() is not None for p in processes):
                raise RuntimeError(f'Worker exited before start; inspect {trial}/worker-*/worker.log')
            if unsafe.is_set() or time.monotonic()-started > cfg['timeout']:
                raise TimeoutError('Memory/temperature threshold or initialization timeout reached')
            time.sleep(.1)
        (trial/'go').touch()
        while any(p.poll() is None for p in processes):
            if unsafe.is_set() or time.monotonic()-started > cfg['timeout']:
                raise TimeoutError('Memory/temperature threshold or trial timeout reached')
            if time.monotonic()-announced > 20:
                print(f'  running {trial.name}: {time.monotonic()-started:.0f}s', flush=True)
                announced = time.monotonic()
            time.sleep(.1)
        if any(p.returncode for p in processes):
            raise RuntimeError(f'Worker failed; inspect {trial}/worker-*/worker.log')
        workers = [read_json(trial/f'worker-{i}'/'result.json') for i in range(cfg['concurrency'])]
        outputs = sorted((o for w in workers for o in w['outputs']), key=lambda x: x['frame_id'])
        wall = max(w['end'] for w in workers)-min(w['start'] for w in workers)
        lo, hi = min(w['start'] for w in workers), max(w['end'] for w in workers)
        active_samples = [s for s in samples if lo <= s['time'] <= hi]
        stages = defaultdict(lambda: {'calls': 0, 'seconds': 0.})
        application_trace = []
        for worker_result in workers:
            for name, row in worker_result['stages'].items():
                stages[name]['calls'] += row['calls']
                stages[name]['seconds'] += row['seconds']
            for row in worker_result['stage_rows']:
                if 'start' in row:
                    application_trace.append({
                        'name': row['stage'], 'cat': 'OCR host stage', 'ph': 'X',
                        'pid': worker_result['worker_id'], 'tid': 1,
                        'ts': (row['start']-lo)*1_000_000, 'dur': row['seconds']*1_000_000,
                        'args': {'frame_id': row['frame_id'], 'scope': 'CPU wall including waits, not GPU kernel'}})
        if application_trace:
            write_json(trial/'host-timeline.json', {'traceEvents': application_trace})
        result = {'config': cfg, 'status': 'success', 'frames': len(outputs), 'wall_seconds': wall,
                  'frames_per_second': len(outputs)/wall,
                  'latency_p50': percentile([o['seconds'] for o in outputs], .50),
                  'latency_p95': percentile([o['seconds'] for o in outputs], .95),
                  'worker_start_skew_seconds': max(w['start'] for w in workers)-min(w['start'] for w in workers),
                  'cpu_seconds': sum(w['cpu_seconds'] for w in workers),
                  'mean_busy_cpu_cores': sum(w['cpu_seconds'] for w in workers)/wall,
                  'init_max_seconds': max(w['init_seconds'] for w in workers),
                  'stages': dict(stages), 'outputs': outputs,
                  'runtime': workers[0]['runtime'], 'ort_version': workers[0]['ort_version'],
                  'gpu_active_samples': active_samples,
                  'gpu_peak_mib': max((s['memory_used_mib'] for s in active_samples), default=None),
                  'gpu_util_mean': statistics.mean(s['gpu_util_percent'] for s in active_samples) if active_samples else None,
                  'gpu_temp_max': max((s['temperature_c'] for s in active_samples), default=None),
                  'profiles': [w.get('profiles', {}) for w in workers],
                  'scope': 'preloaded fixed images, model loading and warmup excluded; aggregate stage times overlap'}
    except BaseException as exc:
        stop_processes(processes)
        result = {'config': cfg, 'status': 'failed', 'error': str(exc), 'traceback': traceback.format_exc()}
        write_json(trial/'result.json', result)
        raise
    finally:
        stopped.set()
        if watcher.is_alive():
            watcher.join(timeout=6)
        for log in logs:
            log.close()
        write_json(trial/'telemetry.json', {'samples': samples, 'monitor_errors': monitor_errors})
    write_json(trial/'result.json', result)
    print(f"{trial.name}: {result['frames']} images, {wall:.3f}s, "
          f"{result['frames_per_second']:.2f} images/s, GPU {result['gpu_peak_mib']} MiB", flush=True)
    return result


def run(args):
    if args.label.startswith('roi-') and args.crop != 'roi':
        raise ValueError('An roi-* experiment label requires --crop roi; refusing a mislabeled trial')
    dataset = Path(args.dataset).resolve()
    manifest = read_json(dataset/'manifest.json')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    levels = [int(n) for n in args.levels.split(',')]
    if min(levels) < 1 or max(levels) > 8 or args.repeats < 1:
        raise ValueError('Concurrency must be 1..8; repeats must be positive')
    available = min(manifest['count'], args.limit or manifest['count'])
    if max(levels) > available:
        raise ValueError('Each worker needs at least one input')
    if args.device == 'cuda':
        # Do not contend with an existing compute workload, even on an otherwise free GPU.
        apps = subprocess.run(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits'],
                              capture_output=True, text=True, timeout=4, check=True).stdout.strip()
        if apps:
            raise RuntimeError(f'GPU compute processes already present: {apps}')
    for repeat in range(args.repeats):
        # Counterbalance order to reduce warm/thermal drift; deterministic replay.
        order = list(levels) if repeat % 2 == 0 else list(reversed(levels))
        for concurrency in order:
            cfg = {'dataset': str(dataset), 'dataset_id': manifest['dataset_id'],
                   'device': args.device, 'concurrency': concurrency, 'repeat': repeat,
                   'label': args.label, 'crop': args.crop, 'det_side': args.det_side,
                   'threads': args.threads, 'rec_batch': args.rec_batch, 'tf32': args.tf32,
                   'force_det_side': args.force_det_side,
                   'use_cls': not args.no_cls, 'limit': args.limit, 'profile': args.profile,
                   'timeout': args.timeout,
                   'trial_dir': str(output/f'{args.label}-c{concurrency}-r{repeat}')}
            one_trial(cfg)


def matrix(args):
    variants = {
        'no-cls': ['--no-cls'],
        'batch16': ['--rec-batch', '16'],
        'tf32': ['--tf32'],
        'det960': ['--det-side', '960'],
        'roi-no-cls': ['--crop', 'roi', '--no-cls'],
        'det1280-effective': ['--det-side', '1280', '--force-det-side'],
        'det960-effective': ['--det-side', '960', '--force-det-side'],
        'no-cls-tf32': ['--no-cls', '--tf32'],
    }
    for name in args.variants.split(','):
        if name not in variants:
            raise ValueError(f'Unknown variant {name}; choose from {list(variants)}')
        command = [sys.executable, str(Path(__file__).resolve()), 'run',
                   '--dataset', str(Path(args.dataset).resolve()), '--output', str(Path(args.output).resolve()),
                   '--label', name, '--levels', '1', '--repeats', str(args.repeats), *variants[name]]
        print('Experiment:', name, flush=True)
        subprocess.run(command, check=True)


def collect_trials(root):
    trials = []
    for path in Path(root).rglob('result.json'):
        if (path.parent/'excluded.json').exists():
            continue
        value = read_json(path)
        if value.get('config') and value.get('status') == 'success':
            value['result_path'] = str(path)
            trials.append(value)
    return trials


def video_trial(args):
    """Full unchanged subtitle algorithm with optional model-level settings.

    Timing includes decode, localization, ROI extraction, visual reuse, merge,
    review and quality gate. This is a separate isolated GPU experiment.
    """
    from app.ocr.ocr_engine import OCREngine
    from app.ocr.profiling import StageRecorder, finish_profiles, force_detection_limit
    from app.services.hard_subtitle_extractor import HardSubtitleExtractor
    os.environ['OCR_DEVICE'] = args.device
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError('Video report exists; choose a fresh output path')
    started = time.perf_counter()
    engine = OCREngine(profile_dir=output.parent/(output.stem+'-profiles') if args.profile else None,
                       use_cls=not args.no_cls, params_override={
                           'Det.limit_side_len': args.det_side,
                           'EngineConfig.onnxruntime.intra_op_num_threads': args.threads,
                           'EngineConfig.onnxruntime.cuda_ep_cfg.use_tf32': args.tf32,
                           'Rec.rec_batch_num': args.rec_batch,
                       })
    if args.force_det_side:
        force_detection_limit(engine, args.det_side)
    if args.no_reuse:
        engine.supports_visual_reuse = False
    initialization = time.perf_counter()-started
    recorder = StageRecorder(engine)
    recorder.frame_id = 'video'
    last_print = [0.]

    def progress(phase, fraction):
        now = time.monotonic()
        if now-last_print[0] >= 20:
            print(f'{phase} {fraction:.1%}, elapsed={time.perf_counter()-started:.1f}s', flush=True)
            last_print[0] = now

    extractor = HardSubtitleExtractor(engine=engine, fps=args.fps)
    result = extractor.extract(args.video, output, progress)
    report = result.diagnostics
    report['experiment'] = {
        'source': str(Path(args.video).resolve()), 'source_sha256': digest(args.video),
        'device': args.device, 'profile': args.profile, 'use_cls': not args.no_cls,
        'det_side': args.det_side, 'threads': args.threads, 'tf32': args.tf32,
        'force_det_side': args.force_det_side,
        'rec_batch': args.rec_batch, 'visual_reuse': not args.no_reuse,
        'init_seconds': initialization, 'total_seconds': time.perf_counter()-started,
        'stages': recorder.summary(),
    }
    if args.profile:
        report['experiment']['profiles'] = finish_profiles(engine)
    write_json(output, report)
    print(json.dumps({'status': result.status, 'message': result.message,
                      'segments': len(result.transcript.segments) if result.transcript else 0,
                      **{k: report.get(k) for k in ('timings', 'visual_reused_frames', 'band_ocr_reads', 'quality_reasons')}},
                     ensure_ascii=False), flush=True)
    if result.status != 'success':
        raise SystemExit(1)


def guarded_video(args):
    if args.video_worker or args.device == 'cpu':
        return review_angles(args) if args.command == 'review' else video_trial(args)
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError('Video report exists; choose a fresh output path')
    apps = subprocess.run(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits'],
                          capture_output=True, text=True, timeout=4, check=True).stdout.strip()
    if apps:
        raise RuntimeError(f'GPU compute processes already present: {apps}')
    gpu_sample()
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], '--video-worker'])
    samples = []
    started = time.monotonic()
    try:
        while process.poll() is None:
            sample = gpu_sample()
            samples.append(sample)
            if (sample['memory_used_mib']/sample['memory_total_mib'] > .91
                    or sample['temperature_c'] >= 85
                    or sample.get('host_available_mib', 99999) < 2000
                    or time.monotonic()-started > args.timeout):
                raise TimeoutError('Video experiment exceeded memory/temperature/timeout threshold')
            time.sleep(.5)
        if process.returncode:
            raise SystemExit(process.returncode)
    finally:
        stop_processes([process])
        write_json(output.with_suffix('.telemetry.json'), {'samples': samples})


def review_angles(args):
    """Replay chosen real frames with identical ROI, toggling only classification."""
    import av
    import cv2
    from app.ocr.ocr_engine import OCREngine
    os.environ['OCR_DEVICE'] = args.device
    baseline = read_json(args.roi_report)
    blocks = baseline['blocks']
    engine = OCREngine()
    angles = []
    original = engine.engine.cls_and_rotate

    def capture(*values, **kwargs):
        images, result = original(*values, **kwargs)
        angles.extend({'label': str(label), 'score': float(score)} for label, score in (result.cls_res or []))
        return images, result

    engine.engine.cls_and_rotate = capture
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with av.open(str(Path(args.video).resolve())) as container:
        stream = container.streams.video[0]
        stream.thread_count = 1
        origin = float((stream.start_time or 0)*stream.time_base)
        for index, target in enumerate(args.times):
            block = next(b for b in blocks if b['start'] <= target < b['end'])
            container.seek(int((target+origin)/stream.time_base), stream=stream, backward=True)
            for frame in container.decode(stream):
                timestamp = float(frame.time)-origin
                if timestamp+1e-5 < target:
                    continue
                image = frame.to_ndarray(format='rgb24')
                h, w = image.shape[:2]
                x1, y1, x2, y2 = block['roi']
                image = image[int(y1*h):int(y2*h), int(x1*w):int(x2*w)].copy()
                break
            else:
                raise ValueError(f'Unable to decode review frame {target}')
            image_path = output.parent/f'angle-review-{index:02d}.png'
            cv2.imwrite(str(image_path), image[:, :, ::-1])
            angles.clear()
            engine.use_cls = True
            with_cls = engine.read(image.copy())
            classified = list(angles)
            engine.use_cls = False
            without_cls = engine.read(image.copy())
            row = {'target': target, 'actual_time': timestamp, 'roi': block['roi'],
                   'image': str(image_path), 'angles': classified,
                   'classification_threshold': float(engine.engine.text_cls.cls_thresh),
                   'with_cls': [{'text': b.text, 'score': b.score} for b in with_cls],
                   'without_cls': [{'text': b.text, 'score': b.score} for b in without_cls]}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    write_json(output, {'runtime': engine.runtime_info, 'tf32': False,
                        'scope': 'identical pixels and ROI, only direction classification toggled', 'rows': rows})


def environment(args):
    import importlib.metadata
    import platform
    import onnxruntime as ort
    packages = {}
    for name in ('onnxruntime-gpu', 'rapidocr', 'numpy', 'opencv-python', 'av',
                 'nvidia-cublas-cu12', 'nvidia-cudnn-cu12'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    versions = {'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
                'cpu_count': os.cpu_count(), 'packages': packages,
                'available_providers': ort.get_available_providers()}
    for name, command in {
        'gpu': ['nvidia-smi', '--query-gpu=name,driver_version,memory.total,power.limit', '--format=csv'],
        'nsys': ['which', 'nsys'], 'ncu': ['which', 'ncu'],
    }.items():
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        versions[name] = result.stdout.strip() if result.returncode == 0 else None
    root = Path(__file__).resolve().parents[1]
    versions['code_sha256'] = {str(path.relative_to(root)): digest(path) for path in (
        Path(__file__).resolve(), root/'app/ocr/profiling.py', root/'app/ocr/ocr_engine.py')}
    versions['model_sha256'] = {p.name: digest(p) for p in (root/'models/ocr').glob('*.onnx')}
    write_json(args.output, versions)
    print(json.dumps(versions, ensure_ascii=False, indent=2))


def render_chart(rows, output):
    """Dependency-free SVG of measured medians; no interpolated experiments."""
    from html import escape
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="690" viewBox="0 0 1080 690">',
           '<rect width="1080" height="690" fill="#fff"/>',
           '<style>text{font:12px sans-serif;fill:#253047}.title{font-size:17px;font-weight:bold}</style>',
           '<text x="24" y="27" class="title">OCR: throughput, latency and GPU memory (medians, profiling OFF)</text>']
    for row_index, (label, title) in enumerate((('baseline', 'Full image'), ('roi-baseline', 'Subtitle ROI'))):
        data = sorted((r for r in rows if r['label'] == label and not r['profile']), key=lambda r: r['concurrency'])
        if not data:
            continue
        for col, (key, name, scale) in enumerate((('median_fps', 'Throughput (images/s)', 1),
                                                  ('median_p95', 'Per-image P95 (ms)', 1000),
                                                  ('peak_gpu_mib', 'GPU memory peak (MiB)', 1))):
            x0, y0 = 60+col*355, 90+row_index*300
            width, height = 270, 190
            vals = [r[key]*scale for r in data if r[key] is not None]
            if not vals:
                continue
            maximum = max(vals)*1.18 or 1
            max_x = max(r['concurrency'] for r in data)
            svg.append(f'<text x="{x0}" y="{y0-30}" class="title">{escape(title)}</text>')
            svg.append(f'<text x="{x0}" y="{y0-10}">{escape(name)}</text>')
            for i in range(5):
                y = y0+height-height*i/4
                svg.append(f'<path d="M{x0},{y} h{width}" stroke="#e3e7ef"/>')
                svg.append(f'<text x="{x0-7}" y="{y+4}" text-anchor="end">{maximum*i/4:.1f}</text>')
            points = []
            for r in data:
                if r[key] is None:
                    continue
                x = x0+width*(r['concurrency']-1)/max(1,max_x-1)
                y = y0+height-height*r[key]*scale/maximum
                points.append(f'{x},{y}')
                svg.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#1765c0"/>')
                label_x = x+6 if r is data[0] else x
                anchor = 'start' if r is data[0] else 'middle'
                svg.append(f'<text x="{label_x}" y="{y-10}" text-anchor="{anchor}">{r[key]*scale:.1f}</text>')
                svg.append(f'<text x="{x}" y="{y0+height+20}" text-anchor="middle">{r["concurrency"]}</text>')
            svg.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="#1765c0" stroke-width="2"/>')
            svg.append(f'<text x="{x0+width/2}" y="{y0+height+43}" text-anchor="middle">Concurrent independent OCR processes</text>')
    svg.append('<text x="24" y="667">Fixed input corpus; loading/warm-up excluded. Memory capacity use is not compute utilization.</text></svg>')
    Path(output).write_text('\n'.join(svg), encoding='utf-8')


def report(args):
    trials = collect_trials(args.root)
    if not trials:
        raise ValueError('No successful trials found')
    groups = defaultdict(list)
    for trial in trials:
        c = trial['config']
        groups[(c['dataset_id'], c['label'], c['concurrency'], c['profile'])].append(trial)
    baseline = {}
    for trial in trials:
        c = trial['config']
        if c['label'] in {'baseline', 'roi-baseline'} and c['concurrency'] == 1 and not c['profile']:
            baseline.setdefault((c['dataset_id'], c['crop']), {o['frame_id']: o for o in trial['outputs']})
    lines = ['# OCR profiling 实验汇总', '',
             '本文件由 scripts/profile_ocr.py 自动生成。具体原因解释与实验条件见人工分析报告。', '',
             '| 配置 | 并发 | profiling | 次数 | 总耗时中位数(s) | 吞吐中位数(图/s) | P95中位数(s) | 显存峰值(MiB) | 字幕文本与基线相同 |',
             '|---|---:|---|---:|---:|---:|---:|---:|---:|']
    summary_rows = []
    for key, rows in sorted(groups.items()):
        identity, label, concurrency, profile = key
        comparisons = []
        similarities = []
        nonempty_comparisons = []
        for row in rows:
            for output in row['outputs']:
                ref = baseline.get((identity, row['config']['crop']), {}).get(output['frame_id'])
                if ref:
                    comparisons.append(output['caption_text'] == ref['caption_text'])
                    similarities.append(SequenceMatcher(None, ref['caption_text'], output['caption_text']).ratio())
                    if ref['caption_text'] or output['caption_text']:
                        nonempty_comparisons.append(output['caption_text'] == ref['caption_text'])
        exact = sum(comparisons)/len(comparisons) if comparisons else None
        peak = max((r['gpu_peak_mib'] for r in rows if r['gpu_peak_mib'] is not None), default=None)
        wall = statistics.median(r['wall_seconds'] for r in rows)
        fps = statistics.median(r['frames_per_second'] for r in rows)
        p95 = statistics.median(r['latency_p95'] for r in rows)
        exact_text = f'{exact:.1%}' if exact is not None else '—'
        lines.append(f'| {label} | {concurrency} | {profile} | {len(rows)} | {wall:.3f} | {fps:.2f} | {p95:.3f} | {peak} | {exact_text} |')
        summary_rows.append({'dataset_id': identity, 'label': label, 'concurrency': concurrency,
                             'profile': profile, 'repeats': len(rows), 'median_wall_seconds': wall,
                             'median_fps': fps, 'median_p95': p95, 'peak_gpu_mib': peak,
                             'caption_exact_vs_baseline': exact,
                             'nonempty_caption_exact_vs_baseline': sum(nonempty_comparisons)/len(nonempty_comparisons) if nonempty_comparisons else None,
                             'nonempty_comparisons': len(nonempty_comparisons),
                             'config': rows[0]['config'],
                             'caption_similarity_mean': statistics.mean(similarities) if similarities else None,
                             'runs': [r['result_path'] for r in rows]})
    lines += ['', '## 阶段计时（每图平均毫秒；多进程累计时间不可当作墙钟时间相加）', '',
              '| 配置 | 并发 | preprocess | detect+crop | det.run | cls+rotate | cls.run | recognize | rec.run | final_output |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for (_, label, concurrency, profile), rows in sorted(groups.items()):
        if profile:
            continue
        values = []
        for name in ('preprocess_img', 'detect_and_crop', 'text_det.run', 'cls_and_rotate',
                     'text_cls.run', 'recognize_txt', 'text_rec.run', 'build_final_output'):
            per_image = [r['stages'].get(name, {}).get('seconds', 0)/r['frames']*1000 for r in rows]
            values.append(f'{statistics.median(per_image):.2f}')
        lines.append(f'| {label} | {concurrency} | ' + ' | '.join(values) + ' |')
    lines += ['', '## 记录范围与限制', '',
              '- 输入为同一份预先解码的固定图片集；模型加载、预热、读图不计入稳态耗时。',
              '- 并发是独立进程和独立 OCR 模型副本；不等于共享 session，也不等于动态批处理。',
              '- 生产 GPU=1 限制未修改；微基准刻意绕过此限制。',
              '- profiling 包含预热事件，只用于定位；性能比较使用 profiling=False 的实验。',
              '- ORT 节点时间为宿主侧事件时间，不是设备 kernel-only 时间；事件数量不是 GPU 利用率。',
              '- 字幕一致性比较相同裁剪方式下指定 ROI 内文本与串行基线，不是人工标注准确率；JSON 另列非空字幕比例。',
              '- GPU 指标为约 0.5 秒采样，可能漏掉瞬时峰值。',
              '- 抽样图片结果不能替代完整视频的解码、定位、复用、合并和质量门禁验证。', '']
    excluded = list(Path(args.root).rglob('excluded.json'))
    if excluded:
        lines += ['## 排除的实验（保留原始记录）', '']
        for path in sorted(excluded):
            lines.append(f'- `{path.parent}`: {read_json(path)["reason"]}')
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('\n'.join(lines), encoding='utf-8')
    write_json(output.with_suffix('.json'), summary_rows)
    if len({r['dataset_id'] for r in summary_rows}) == 1:
        render_chart(summary_rows, output.with_suffix('.svg'))
    print(output.resolve())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('video')
    p.add_argument('--dataset', required=True)
    p.add_argument('--count', type=int, default=96)
    p.add_argument('--start', type=float, default=0)
    p.add_argument('--end', type=float)
    p.add_argument('--roi', type=float, nargs=4, default=[.10, .79, .90, .92])
    p.set_defaults(func=prepare)
    p = sub.add_parser('run')
    p.add_argument('--dataset', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--label', default='baseline')
    p.add_argument('--levels', default='1,2,3')
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--device', choices=['cpu', 'cuda'], default='cuda')
    p.add_argument('--profile', action='store_true')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--crop', choices=['full', 'roi'], default='full')
    p.add_argument('--det-side', type=int, default=1280)
    p.add_argument('--force-det-side', action='store_true')
    p.add_argument('--threads', type=int, default=1)
    p.add_argument('--rec-batch', type=int, default=6)
    p.add_argument('--tf32', action='store_true')
    p.add_argument('--no-cls', action='store_true')
    p.add_argument('--timeout', type=float, default=600)
    p.set_defaults(func=run)
    p = sub.add_parser('matrix')
    p.add_argument('--dataset', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--repeats', type=int, default=2)
    p.add_argument('--variants', default='no-cls,batch16,tf32,det960,roi-no-cls')
    p.set_defaults(func=matrix)
    p = sub.add_parser('report')
    p.add_argument('--root', required=True)
    p.add_argument('--output', required=True)
    p.set_defaults(func=report)
    p = sub.add_parser('video')
    p.add_argument('video')
    p.add_argument('--output', required=True)
    p.add_argument('--device', choices=['cpu', 'cuda'], default='cuda')
    p.add_argument('--profile', action='store_true')
    p.add_argument('--fps', type=float, default=5)
    p.add_argument('--det-side', type=int, default=1280)
    p.add_argument('--force-det-side', action='store_true')
    p.add_argument('--threads', type=int, default=1)
    p.add_argument('--rec-batch', type=int, default=6)
    p.add_argument('--tf32', action='store_true')
    p.add_argument('--no-cls', action='store_true')
    p.add_argument('--no-reuse', action='store_true')
    p.add_argument('--timeout', type=float, default=900)
    p.add_argument('--video-worker', action='store_true', help=argparse.SUPPRESS)
    p.set_defaults(func=guarded_video)
    p = sub.add_parser('review')
    p.add_argument('video')
    p.add_argument('--roi-report', required=True)
    p.add_argument('--times', nargs='+', type=float, required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--device', choices=['cpu', 'cuda'], default='cuda')
    p.add_argument('--timeout', type=float, default=120)
    p.add_argument('--video-worker', action='store_true', help=argparse.SUPPRESS)
    p.set_defaults(func=guarded_video)
    p = sub.add_parser('environment')
    p.add_argument('--output', required=True)
    p.set_defaults(func=environment)
    p = sub.add_parser('_worker')
    p.add_argument('--config', required=True)
    p.add_argument('--worker-id', type=int, required=True)
    p.add_argument('--worker-dir', required=True)
    p.set_defaults(func=worker)
    args = parser.parse_args()
    if args.command == 'prepare' and not (0 <= args.roi[0] < args.roi[2] <= 1 and 0 <= args.roi[1] < args.roi[3] <= 1):
        parser.error('ROI must be normalized x1 y1 x2 y2 within [0,1]')
    if args.command == 'run' and (args.threads < 1 or args.det_side < 32 or args.rec_batch < 1 or args.limit < 0):
        parser.error('Invalid thread/detection/batch/limit configuration')
    args.func(args)


if __name__ == '__main__':
    main()
