"""Build a fixed multi-video, multi-resolution frame dataset for OCR benchmarks.

Unlike profile_ocr.py prepare (single video), this samples uniformly across
several sources so the corpus covers 768x480, 1920x1080 and 1080x1920 input
shapes. Frames are stored as raw .npy RGB arrays with a manifest that pins
every sample by SHA-256, so a rerun with the same inputs is reproducible.

Usage (from backend):
  python scripts/bench_ocr_prepare_mixed.py \
      --output data/ocr_bench_2026-10-31/frames --per-video 24 \
      --roi 0.10 0.79 0.90 0.92
"""
import argparse
import hashlib
import json
from pathlib import Path
import time


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def sample_video(video, count, start_fraction, end_fraction):
    """Uniformly sample `count` frames between the two duration fractions."""
    import av
    import numpy as np
    frames = []
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        stream.thread_count = 1
        origin = float((stream.start_time or 0) * stream.time_base)
        duration = (float(stream.duration * stream.time_base) if stream.duration
                    else float(container.duration or 0) / av.time_base)
        if duration <= 0:
            raise ValueError(f'无法读取时长: {video}')
        begin = duration * start_fraction
        end = duration * end_fraction
        if end <= begin:
            raise ValueError(f'无效采样区间: {video}')
        for index in range(count):
            target = begin + (end - begin) * (index + .5) / count
            container.seek(int((target + origin) / stream.time_base), stream=stream, backward=True)
            for frame in container.decode(stream):
                timestamp = float(frame.time) - origin
                if timestamp + 1e-5 < target:
                    continue
                image = frame.to_ndarray(format='rgb24')
                frames.append({'time': round(timestamp, 3), 'array': image,
                               'shape': list(image.shape), 'source': video.name})
                break
            else:
                raise ValueError(f'{video} 无法解码 {target:.2f}s 处的画面')
    return frames, duration


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--per-video', type=int, default=24)
    parser.add_argument('--roi', type=float, nargs=4, default=[0.10, 0.79, 0.90, 0.92])
    parser.add_argument('--start-fraction', type=float, default=0.05)
    parser.add_argument('--end-fraction', type=float, default=0.95)
    parser.add_argument('videos', nargs='+')
    args = parser.parse_args()

    import numpy as np
    out = Path(args.output).resolve()
    if (out / 'manifest.json').exists():
        raise SystemExit('数据集已存在，请换一个目录以保留原有身份')
    out.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    records = []
    sources = []
    for video in args.videos:
        path = Path(video).resolve()
        sampled, duration = sample_video(path, args.per_video,
                                         args.start_fraction, args.end_fraction)
        for item in sampled:
            index = len(records)
            filename = f'{index:05d}.npy'
            np.save(out / filename, item['array'], allow_pickle=False)
            del item['array']
            records.append({'id': index, 'path': filename, 'time': item['time'],
                            'shape': item['shape'], 'source': item['source'],
                            'sha256': digest(out / filename)})
        sources.append({'path': str(path), 'name': path.name, 'sha256': digest(path),
                        'duration': round(duration, 3),
                        'shape': records[-1]['shape']})
        print(f'{path.name}: {len(sampled)} 帧, {duration:.1f}s, {records[-1]["shape"]}', flush=True)

    identity = hashlib.sha256(json.dumps(
        [{k: r[k] for k in ('time', 'shape', 'source', 'sha256')} for r in records],
        sort_keys=True).encode()).hexdigest()
    manifest = {'kind': 'multi_video', 'count': len(records), 'roi': args.roi,
                'per_video': args.per_video, 'dataset_id': identity,
                'sources': sources, 'frames': records,
                'prepare_seconds': round(time.perf_counter() - started, 3)}
    (out / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    shapes = {}
    for record in records:
        key = f'{record["shape"][0]}x{record["shape"][1]}'
        shapes[key] = shapes.get(key, 0) + 1
    print(json.dumps({'count': len(records), 'sources': len(sources),
                      'shapes': shapes, 'dataset_id': identity}, ensure_ascii=False))


if __name__ == '__main__':
    main()
