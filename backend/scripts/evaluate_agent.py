"""Paired, real-model RAG / production Agent evaluation on isolated note copies.

No production users, indexes, histories or provider records are modified. Imports
are deferred so dataset validation / metric tests need only the standard library.
Credentials are read locally and never written to the report. See AGENT_EVALUATION.md.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import ContextVar
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import statistics
import sys
import time
import unicodedata
from unittest.mock import patch
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = BACKEND / 'evals' / 'agent_qa_v1.json'
TRACE = ContextVar('agent_eval_trace', default=None)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text).lower())


def timestamps(text):
    """Read explicit time expressions, never bare numbers or model percentages."""
    result = []
    for match in re.finditer(r'(?<![\d:])(\d{1,3}):(\d{2})(?::(\d{2}(?:\.\d+)?))?(?![\d:])', text):
        a, b, c = match.groups()
        result.append(int(a) * (3600 if c is not None else 60) + int(b) * (60 if c is not None else 1) + (float(c) if c else 0))
    for match in re.finditer(r'(\d+(?:\.\d+)?)\s*分(?:钟)?\s*(\d+(?:\.\d+)?)\s*秒', text):
        result.append(float(match[1]) * 60 + float(match[2]))
    for match in re.finditer(r'(?<![\d.])(\d+(?:\.\d+)?)\s*(?:秒|s\b)', text):
        result.append(float(match[1]))
    return result


def score(case, record):
    answer = normalized(record.get('answer', ''))
    groups = [any(re.search(pattern, answer) for pattern in alternatives)
              for alternatives in case['answer_groups']]
    forbidden = [p for p in case.get('forbidden_patterns', []) if re.search(p, answer)]
    times = timestamps(record.get('answer', ''))
    time_checks = [any(abs(t - expected) <= case.get('time_tolerance', 2) for t in times)
                   for expected in case.get('time_seconds', [])]
    trace = record.get('trace', [])
    evidence_text = '\n'.join(
        '\n'.join(c['text'] for c in e['chunks']) if e['kind'] == 'retrieval' else e['result']
        for e in trace if e['kind'] in {'retrieval', 'tool'})
    evidence_checks = [any(normalized(term) in normalized(evidence_text) for term in group)
                       for group in case.get('evidence_groups', [])]
    # Errors and empty/truncated responses remain failures in the denominator.
    valid = bool(answer) and not record.get('error') and not record.get('truncated', False)
    return {'passed': valid and all(groups) and not forbidden and all(time_checks),
            'fact_groups': groups, 'time_checks': time_checks,
            'forbidden_matches': forbidden, 'evidence_checks': evidence_checks,
            'evidence_available': all(evidence_checks) if evidence_checks else None}


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * p
    lo, hi = math.floor(position), math.ceil(position)
    return round(values[lo] + (values[hi] - values[lo]) * (position - lo), 4)


def summarize(records):
    modes = {}
    for mode in sorted({r['mode'] for r in records}):
        rows = [r for r in records if r['mode'] == mode]
        events = [e for r in rows for e in r['trace']]
        calls = [e for e in events if e['kind'] == 'completion']
        tools = [e for e in events if e['kind'] == 'tool']
        latencies = [r['elapsed_seconds'] for r in rows]
        evidence = [r['score']['evidence_available'] for r in rows if r['score']['evidence_available'] is not None]
        categories = {}
        for category in sorted({r['category'] for r in rows}):
            subset = [r for r in rows if r['category'] == category]
            categories[category] = {'total': len(subset), 'passed': sum(r['score']['passed'] for r in subset)}
        modes[mode] = {
            'total': len(rows), 'passed': sum(r['score']['passed'] for r in rows),
            'pass_rate': sum(r['score']['passed'] for r in rows) / len(rows),
            'errors': sum(bool(r.get('error')) for r in rows),
            'truncated': sum(r.get('truncated', False) for r in rows),
            'evidence_total': len(evidence), 'evidence_available': sum(evidence),
            'latency_mean_seconds': round(statistics.mean(latencies), 4),
            'latency_p50_seconds': percentile(latencies, .5),
            'latency_p95_seconds': percentile(latencies, .95),
            'completion_requests': len(calls),
            'usage_missing_requests': sum(e.get('usage') is None for e in calls),
            'prompt_tokens': sum((e.get('usage') or {}).get('prompt_tokens', 0) or 0 for e in calls),
            'completion_tokens': sum((e.get('usage') or {}).get('completion_tokens', 0) or 0 for e in calls),
            'total_tokens': sum((e.get('usage') or {}).get('total_tokens', 0) or 0 for e in calls),
            'tool_calls': len(tools), 'tool_calls_successful': sum(e['success'] for e in tools),
            'questions_using_tools': sum(any(e['kind'] == 'tool' for e in r['trace']) for r in rows),
            'categories': categories,
        }
    paired = {}
    index = {(r['case_id'], r['repeat'], r['mode']): r for r in records}
    for mode in modes:
        if mode == 'rag':
            continue
        pairs = [(r, index.get((r['case_id'], r['repeat'], 'rag'))) for r in records if r['mode'] == mode]
        pairs = [(a, b) for a, b in pairs if b is not None]
        paired[mode] = {'pairs': len(pairs),
                        'agent_only_pass': sum(a['score']['passed'] and not b['score']['passed'] for a, b in pairs),
                        'rag_only_pass': sum(b['score']['passed'] and not a['score']['passed'] for a, b in pairs)}
    return {'modes': modes, 'paired': paired}


def validate_dataset(dataset):
    videos = {v['id']: v for v in dataset['videos']}
    if len(videos) != len(dataset['videos']):
        raise ValueError('Duplicate video IDs')
    ids = set()
    for case in dataset['cases']:
        if case['id'] in ids or case['video'] not in videos or not case['answer_groups']:
            raise ValueError('Invalid or duplicate case: ' + case['id'])
        ids.add(case['id'])
        for group in case['answer_groups']:
            if not group:
                raise ValueError('Empty fact group: ' + case['id'])
            for expression in group:
                re.compile(expression)
        for expression in case.get('forbidden_patterns', []):
            re.compile(expression)
    return videos


def prepare_corpus(dataset, source_root, work):
    """Snapshot only necessary public-video fields, strip download headers/paths."""
    notes = work / 'notes'
    notes.mkdir(parents=True, exist_ok=True)
    manifest = []
    for video in dataset['videos']:
        source = (source_root / video['source_note']).resolve()
        if not source.is_relative_to(source_root.resolve()):
            raise ValueError('Corpus source escapes root')
        original = json.loads(source.read_text(encoding='utf-8'))
        audio = original.get('audio_meta') or {}
        raw = audio.get('raw_info') or {}
        safe_audio = {k: audio[k] for k in ('title', 'duration', 'platform', 'video_id') if k in audio}
        safe_audio['raw_info'] = {k: raw[k] for k in ('title', 'uploader', 'description', 'tags', 'webpage_url') if k in raw}
        segments = [{k: s[k] for k in ('start', 'end', 'text')} for s in original['transcript']['segments']]
        snapshot = {'markdown': original['markdown'], 'transcript': {'segments': segments}, 'audio_meta': safe_audio}
        target = notes / (video['task_id'] + '.json')
        contents = json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n'
        if target.exists() and target.read_text(encoding='utf-8') != contents:
            raise ValueError('Frozen corpus changed; use a fresh --output directory')
        target.write_text(contents, encoding='utf-8')
        for case in [c for c in dataset['cases'] if c['video'] == video['id']]:
            for group in case.get('evidence_groups', []):
                if not any(normalized(term) in normalized(contents) for term in group):
                    raise ValueError('Missing reference evidence: ' + case['id'] + ' ' + repr(group))
        manifest.append({'video': video['id'], 'task_id': video['task_id'], 'title': safe_audio.get('title'),
                         'url': raw.get('webpage_url'), 'duration_seconds': audio.get('duration'),
                         'segments': len(segments), 'source_sha256': digest(source), 'snapshot_sha256': digest(target)})
    return manifest


def provider_config(database, provider_id):
    with sqlite3.connect('file:' + str(database.resolve()) + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT name, type, base_url, api_key FROM providers WHERE id=? AND enabled=1', (provider_id,)).fetchone()
    if not row or not row[3]:
        raise ValueError('Selected provider is unavailable or has no API key')
    return dict(zip(('name', 'type', 'base_url', 'api_key'), row))


def install_runtime(work):
    # Must happen before importing application modules (which read env at import).
    os.environ.update(DATABASE_URL='sqlite:///' + str(work / 'eval.db'),
                      NOTE_OUTPUT_DIR=str(work / 'notes'), VECTOR_DB_DIR=str(work / 'vectors'),
                      TASK_RUNTIME_DIR=str(work / 'runtime'), EMBEDDING_ALLOW_DOWNLOAD='0',
                      EMBEDDING_MODEL_DIR=str(BACKEND / 'models/chroma/onnx_models/all-MiniLM-L6-v2'))
    os.chdir(work)
    sys.path.insert(0, str(BACKEND))
    from app.services import chat_service as service
    from app.utils.secrets import register_secret
    return service, register_secret


def instrument(service, args):
    factory = service.GPTFactory.from_config
    query = service.VectorStoreManager.query
    execute = service.execute_tool

    def traced_factory(config):
        gpt = factory(config)
        original = gpt.client.chat.completions.create

        def complete(**kwargs):
            kwargs.update(temperature=args.temperature, max_tokens=args.max_tokens)
            started = time.monotonic()
            event = {'kind': 'completion'}
            try:
                response = original(**kwargs)
                event.update(model=response.model, finish_reason=response.choices[0].finish_reason,
                             usage=response.usage.model_dump() if response.usage else None)
                return response
            except Exception as exc:
                event['error_type'] = type(exc).__name__
                raise
            finally:
                event['elapsed_seconds'] = round(time.monotonic() - started, 4)
                TRACE.get().append(event)

        gpt.client.chat.completions.create = complete
        TRACE.get().append({'kind': 'client', 'client': gpt.client})
        return gpt

    def traced_query(store, *a, **kw):
        started = time.monotonic()
        chunks = query(store, *a, **kw)
        if TRACE.get() is not None:
            TRACE.get().append({'kind': 'retrieval', 'chunks': chunks,
                                'elapsed_seconds': round(time.monotonic() - started, 4)})
        return chunks

    def traced_tool(task_id, name, arguments, **kwargs):
        started = time.monotonic()
        event = {'kind': 'tool', 'name': name, 'arguments': arguments, 'success': False, 'result': ''}
        try:
            result = execute(task_id, name, arguments, **kwargs)
            event['result'] = result
            try:
                parsed = json.loads(result)
                event['success'] = isinstance(parsed, dict) and 'error' not in parsed
            except (ValueError, TypeError):
                pass
            return result
        except Exception as exc:
            event['error_type'] = type(exc).__name__
            raise
        finally:
            event['elapsed_seconds'] = round(time.monotonic() - started, 4)
            TRACE.get().append(event)

    return [patch.object(service.GPTFactory, 'from_config', traced_factory),
            patch.object(service.VectorStoreManager, 'query', traced_query),
            patch.object(service, 'execute_tool', traced_tool)]


def baseline(service, case, video, args):
    chunks = service.VectorStoreManager().query(video['task_id'], case['question'], n_results=6)
    # Same retrieved context and language/style requirements; remove instructions
    # that ask a tool-less baseline to invoke unavailable tools.
    prompt = ('你是一个视频笔记问答助手。系统已自动检索了一些相关内容作为参考。\n'
              '--- 初始检索内容 ---\n' + service._build_context(chunks) + '\n---\n'
              '回答要求：\n- 本轮没有可用工具，请根据参考内容回答，信息不足时请说明。\n'
              '- 请用中文回答，保持简洁准确')
    provider = service.ProviderService.get_provider_by_id(args.provider)
    config = service.ModelConfig(api_key=provider['api_key'], base_url=provider['base_url'],
                                 model_name=args.model, provider=provider['type'], name=provider['name'])
    gpt = service.GPTFactory.from_config(config)
    with service.resources('api', timeout=args.timeout):
        result = gpt.client.chat.completions.create(model=gpt.model, messages=[
            {'role': 'system', 'content': prompt}, {'role': 'user', 'content': case['question']}],
            timeout=args.timeout)
    return {'answer': result.choices[0].message.content or '', 'sources': service._build_sources(chunks)}


def run_case(service, case, video, mode, repeat, args):
    events = []
    token = TRACE.set(events)
    started = time.monotonic()
    record = {'case_id': case['id'], 'video': case['video'], 'category': case['category'],
              'mode': mode, 'repeat': repeat, 'question': case['question']}
    try:
        if mode == 'agent':
            result = service.chat(video['task_id'], case['question'], [], args.provider,
                                  args.model, timeout_seconds=args.timeout)
        else:
            result = baseline(service, case, video, args)
        record.update(result)
    except Exception as exc:
        # SDK exception text can contain provider request bodies. Keep type/status only.
        record.update(answer='', sources=[], error={'type': type(exc).__name__, 'status': getattr(exc, 'status_code', None)})
    finally:
        for event in events:
            if event['kind'] == 'client':
                event['client'].close()
        record['elapsed_seconds'] = round(time.monotonic() - started, 4)
        record['trace'] = [e for e in events if e['kind'] != 'client']
        record['truncated'] = any(e.get('finish_reason') == 'length' for e in record['trace'])
        TRACE.reset(token)
    record['score'] = score(case, record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--source-root', type=Path, default=BACKEND / 'note_results')
    parser.add_argument('--provider-db', type=Path, default=BACKEND / 'bili_note.db')
    parser.add_argument('--provider', default='qwen')
    parser.add_argument('--model', default='deepseek-v4-pro')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--temperature', type=float, default=0)
    parser.add_argument('--max-tokens', type=int, default=8192)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--case-id', action='append', help='Select case IDs for a separate diagnostic run')
    parser.add_argument('--modes', nargs='+', choices=['rag', 'agent'], default=['rag', 'agent'])
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if args.workers < 1 or args.repeats < 1 or args.timeout <= 0 or args.max_tokens < 1:
        parser.error('workers, repeats, timeout, max-tokens must be positive')
    args.dataset = args.dataset.resolve()
    args.provider_db = args.provider_db.resolve()
    args.source_root = args.source_root.resolve()
    work = args.output.resolve()
    work.mkdir(parents=True, exist_ok=True)
    dataset = json.loads(args.dataset.read_text(encoding='utf-8'))
    videos = validate_dataset(dataset)
    manifest = prepare_corpus(dataset, args.source_root, work)
    (work / 'corpus_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.validate_only:
        print(json.dumps({'videos': len(videos), 'cases': len(dataset['cases']), 'validated': True}))
        return
    if (work / 'results.jsonl').exists():
        parser.error('Output already contains results; choose a fresh directory to preserve evidence')
    config = provider_config(args.provider_db, args.provider)
    service, register_secret = install_runtime(work)
    register_secret(config['api_key'])
    # Every thread shares immutable provider configuration, but creates its own client.
    # No ORM access to the production provider database is needed after this point.
    from contextlib import ExitStack
    with ExitStack() as stack:
        stack.enter_context(patch.object(service.ProviderService, 'get_provider_by_id', lambda _: dict(config)))
        for instrument_patch in instrument(service, args):
            stack.enter_context(instrument_patch)
        # Build / warm indexes before timed questions; never touch production indexes.
        store = service.VectorStoreManager()
        for video in dataset['videos']:
            store.index_task(video['task_id'])
        cases = dataset['cases'][:args.limit] if args.limit else dataset['cases']
        if args.case_id:
            cases = [c for c in cases if c['id'] in args.case_id]
            if {c['id'] for c in cases} != set(args.case_id):
                parser.error('Unknown or excluded --case-id')
        modes = list(dict.fromkeys(args.modes))
        jobs = [(case, mode, repeat) for case in cases for repeat in range(args.repeats) for mode in modes]
        random.Random(20261105).shuffle(jobs)
        started = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        metadata = {'started_at': started, 'dataset_sha256': digest(args.dataset), 'runner_sha256': digest(__file__),
                    'chat_service_sha256': digest(service.__file__), 'model_requested': args.model,
                    'provider_id': args.provider, 'temperature': args.temperature, 'max_tokens': args.max_tokens,
                    'timeout_seconds': args.timeout, 'workers': args.workers, 'repeats': args.repeats,
                    'case_count': len(cases), 'modes': modes, 'sample_order_seed': 20261105,
                    'python': sys.version, 'warm_index': True, 'empty_history': True,
                    'pricing': None, 'cost_note': 'No verified tariff; report tokens, not invented currency costs.'}
        (work / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        records = []
        with (work / 'results.jsonl').open('w', encoding='utf-8') as output, ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(run_case, service, c, videos[c['video']], mode, repeat, args) for c, mode, repeat in jobs]
            for future in as_completed(futures):
                record = future.result()
                records.append(record)
                output.write(json.dumps(record, ensure_ascii=False) + '\n')
                output.flush()
                print(json.dumps({'completed': len(records), 'total': len(jobs), 'case': record['case_id'],
                                  'mode': record['mode'], 'passed': record['score']['passed'],
                                  'elapsed': record['elapsed_seconds']}, ensure_ascii=False), flush=True)
        summary = summarize(records)
        summary['metadata'] = metadata
        summary['results_sha256'] = digest(work / 'results.jsonl')
        (work / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
