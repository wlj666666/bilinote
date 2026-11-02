"""Aggregate ORT profiling traces into per-operator attribution.

Reads the trace JSON files that ONNX Runtime writes when a session has
`enable_profiling = True` and reports, for each model and each phase:

  * node event counts grouped by Execution Provider
  * host-side node duration grouped by operator type, with share of the total
  * the kernel / operator names that dominate each stage

Scope and limits (must stay in the report):
  * ORT `dur` is host-side node event time, NOT CUDA kernel-only device time.
  * With several workers, node times overlap and must never be summed as wall
    clock. This script therefore aggregates per worker and per model, and only
    reports *within-trace shares*, which stay meaningful under overlap.

Usage (from backend):
  python scripts/bench_ocr_operator_report.py \
      --root data/ocr_bench_2026-10-31/op-profiles \
      --output data/ocr_bench_2026-10-31/operator-attribution.json
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


def summarize_trace(path):
    """Return provider counts and operator durations for one ORT trace file."""
    events = json.loads(Path(path).read_text(encoding='utf-8'))
    providers = Counter()
    operators = defaultdict(lambda: {'events': 0, 'host_ms': 0.0})
    total_ms = 0.0
    node_events = 0
    for event in events:
        if event.get('cat') != 'Node':
            continue
        args = event.get('args') or {}
        provider = args.get('provider')
        if not provider:
            continue
        node_events += 1
        providers[provider] += 1
        duration = float(event.get('dur') or 0.) / 1000.
        name = event.get('name') or args.get('op_name') or 'unknown'
        operators[name]['events'] += 1
        operators[name]['host_ms'] += duration
        total_ms += duration
    ordered = sorted(operators.items(), key=lambda item: item[1]['host_ms'], reverse=True)
    return {
        'trace': Path(path).name,
        'node_events': node_events,
        'providers': dict(providers),
        'host_node_ms_total': round(total_ms, 3),
        'operators': [
            {'op': name, 'events': row['events'], 'host_ms': round(row['host_ms'], 3),
             'share': round(row['host_ms'] / total_ms, 4) if total_ms else None}
            for name, row in ordered
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--top', type=int, default=12)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    grouped = defaultdict(list)
    for path in sorted(root.rglob('*.json')):
        if 'profiles' not in path.parts:
            continue
        model = path.stem.split('_')[0]
        worker = path.parents[1].name if len(path.parents) > 1 else 'unknown'
        grouped[(worker, model)].append(summarize_trace(path))

    report = {'scope': {
        'duration': 'ORT host-side node event time, not CUDA kernel-only time',
        'aggregation': 'per worker and per model; shares stay valid under overlap',
        'warning': 'Do not sum host_ms across workers or repeats as wall clock',
    }, 'groups': {}}

    for (worker, model), traces in sorted(grouped.items()):
        merged = defaultdict(lambda: {'events': 0, 'host_ms': 0.0})
        providers = Counter()
        total = 0.0
        for trace in traces:
            providers.update(trace['providers'])
            total += trace['host_node_ms_total']
            for row in trace['operators']:
                merged[row['op']]['events'] += row['events']
                merged[row['op']]['host_ms'] += row['host_ms']
        ordered = sorted(merged.items(), key=lambda item: item[1]['host_ms'], reverse=True)
        report['groups'].setdefault(worker, {})[model] = {
            'traces': len(traces),
            'providers': dict(providers),
            'host_node_ms_total': round(total, 3),
            'top_operators': [
                {'op': name, 'events': row['events'], 'host_ms': round(row['host_ms'], 3),
                 'share': round(row['host_ms'] / total, 4) if total else None}
                for name, row in ordered[:args.top]
            ],
        }

    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    for worker, models in report['groups'].items():
        for model, data in models.items():
            print(f'{worker} / {model}: traces={data["traces"]} '
                  f'host_ms={data["host_node_ms_total"]:.1f} providers={data["providers"]}')
            for row in data['top_operators'][:6]:
                print(f'    {row["op"]:<24} share={row["share"]:.1%} '
                      f'events={row["events"]} host_ms={row["host_ms"]:.1f}')
    print(f'\nwritten: {out}')


if __name__ == '__main__':
    main()
