"""Resize an existing frame dataset to a fixed width, preserving identity.

Production's detection scan decodes full frames downscaled to max_width=1280
before OCR. This derives a matching corpus so the scan phase can be benchmarked
separately from the ROI crop phase.

Usage (from backend):
  python scripts/bench_ocr_resize.py \
      --source data/ocr_bench_2026-10-31/frames \
      --output data/ocr_bench_2026-10-31/frames-1280 --width 1280
"""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--width', type=int, default=1280)
    args = parser.parse_args()

    import cv2
    import numpy as np
    cv2.setNumThreads(1)

    source = Path(args.source).resolve()
    out = Path(args.output).resolve()
    if (out / 'manifest.json').exists():
        raise SystemExit('目标数据集已存在，请换一个目录')
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))

    frames = []
    for record in manifest['frames']:
        image = np.load(source / record['path'], allow_pickle=False)
        height, width = image.shape[:2]
        if width > args.width:
            target_height = round(height * args.width / width)
            image = cv2.resize(image, (args.width, target_height), interpolation=cv2.INTER_AREA)
        filename = record['path']
        np.save(out / filename, image, allow_pickle=False)
        frames.append({**record, 'shape': list(image.shape),
                       'source_shape': record['shape'],
                       'sha256': digest(out / filename)})

    identity = hashlib.sha256(json.dumps(
        [{k: f[k] for k in ('time', 'shape', 'source', 'sha256')} for f in frames],
        sort_keys=True).encode()).hexdigest()
    result = {**manifest, 'kind': 'resized', 'resized_width': args.width,
              'derived_from': manifest['dataset_id'], 'dataset_id': identity,
              'frames': frames}
    (out / 'manifest.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    shapes = {}
    for frame in frames:
        key = f'{frame["shape"][0]}x{frame["shape"][1]}'
        shapes[key] = shapes.get(key, 0) + 1
    print(json.dumps({'count': len(frames), 'shapes': shapes,
                      'dataset_id': identity}, ensure_ascii=False))


if __name__ == '__main__':
    main()
