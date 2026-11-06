"""Recompute the full paired report from immutable raw runs, without model calls."""
import argparse
import hashlib
import json
from pathlib import Path

from evaluate_agent import score, summarize, validate_dataset


def read_run(path):
    metadata = json.loads((path / 'metadata.json').read_text(encoding='utf-8'))
    manifest = json.loads((path / 'corpus_manifest.json').read_text(encoding='utf-8'))
    rows = [json.loads(line) for line in (path / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
    return metadata, manifest, rows


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report(dataset, raw_runs):
    """Score all configurations with the same rubric; preserve original scores."""
    validate_dataset(dataset)
    cases = {c['id']: c for c in dataset['cases']}
    rows, sources = [], []
    for name, run, selected_mode in raw_runs:
        metadata, manifest, raw_rows = read_run(run)
        sources.append({'configuration': name, 'run': run.name, 'metadata': metadata,
                        'corpus_manifest': manifest, 'results_sha256': file_digest(run / 'results.jsonl')})
        selected = [r for r in raw_rows if r['mode'] == selected_mode]
        keys = [(r['case_id'], r['repeat']) for r in selected]
        expected = {(c, repeat) for c in cases for repeat in range(metadata['repeats'])}
        if set(keys) != expected or len(keys) != len(set(keys)):
            raise ValueError('Incomplete or duplicate run: ' + name)
        for raw in selected:
            row = dict(raw)
            if row['question'] != cases[row['case_id']]['question']:
                raise ValueError('Question changed: ' + row['case_id'])
            row['mode'] = name
            row['original_score'] = raw['score']
            row['score'] = score(cases[row['case_id']], row)
            rows.append(row)
    # A regenerated index must still use identical note snapshots and query inputs.
    manifests = [{v['video']: v['snapshot_sha256'] for v in s['corpus_manifest']} for s in sources]
    if any(m != manifests[0] for m in manifests[1:]):
        raise ValueError('Corpus snapshots differ between compared runs')
    signatures = [(s['metadata']['model_requested'], s['metadata']['temperature'],
                   s['metadata']['max_tokens'], s['metadata']['timeout_seconds'],
                   s['metadata']['repeats']) for s in sources]
    if len(set(signatures)) != 1:
        raise ValueError('Model or generation settings differ')
    retrieval = {}
    for row in rows:
        chunks = next(e['chunks'] for e in row['trace'] if e['kind'] == 'retrieval')
        signature = [(c['text'], c.get('metadata')) for c in chunks]
        retrieval.setdefault((row['case_id'], row['repeat']), []).append(signature)
    same_retrieval = all(all(s == signatures[0] for s in signatures[1:]) for signatures in retrieval.values())
    result = summarize(rows)
    result.update(rubric_version=dataset['version'], sources=sources,
                  identical_initial_retrieval=same_retrieval,
                  reference_scoring='Fixed fact groups plus explicit timestamp checks; not a general semantic accuracy metric')
    return result, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rag-run', type=Path, required=True)
    parser.add_argument('--original-run', type=Path, required=True)
    parser.add_argument('--agent-run', type=Path, required=True)
    parser.add_argument('--rubric', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    dataset = json.loads(args.rubric.read_text(encoding='utf-8'))
    result, rows = build_report(dataset, [('rag', args.rag_run, 'rag'),
                                        ('agent_original', args.original_run, 'agent'),
                                        ('agent_fixed', args.agent_run, 'agent')])
    result['rubric_sha256'] = file_digest(args.rubric)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (args.output / 'answers.jsonl').open('w', encoding='utf-8') as output:
        for row in sorted(rows, key=lambda r: (r['case_id'], r['repeat'], r['mode'])):
            compact = {k: v for k, v in row.items() if k not in ('trace', 'sources')}
            compact['tool_calls'] = [{k: v for k, v in e.items() if k != 'result'} for e in row['trace'] if e['kind'] == 'tool']
            compact['completion_calls'] = [e for e in row['trace'] if e['kind'] == 'completion']
            output.write(json.dumps(compact, ensure_ascii=False) + '\n')
    print(json.dumps({'modes': result['modes'], 'paired': result['paired'],
                      'identical_initial_retrieval': result['identical_initial_retrieval']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
