"""Isolated HTTP stability tests; never call external services or modify user tasks.

Run: python -m pytest tests/test_stability.py -q
The child server uses real API routes/executor with a controllable worker.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def serve():
    import uvicorn
    from fastapi import FastAPI
    from app.routers import note as router
    from app.services.note import NoteGenerator
    from app.models.notes_model import NoteResult
    from app.models.audio_model import AudioDownloadResult
    from app.models.transcriber_model import TranscriptResult, TranscriptSegment
    from app.services.task_recovery import recover_interrupted_tasks
    import types

    release = threading.Event()
    counts = {}
    active = 0
    peak_active = 0
    state_lock = threading.Lock()
    class ControlledGenerator(NoteGenerator):
        def generate(self, **kwargs):
            nonlocal active, peak_active
            task_id = kwargs['task_id']
            with state_lock:
                counts[task_id] = counts.get(task_id, 0) + 1
                active += 1
                peak_active = max(peak_active, active)
            try:
                self._update_status(task_id, router.TaskStatus.SUMMARIZING)
                if not release.wait(15):
                    self._update_status(task_id, router.TaskStatus.FAILED, 'test worker timed out')
                    return None
                if task_id.startswith('fail-'):
                    raise RuntimeError('injected worker failure')
                return NoteResult('summary '+task_id,
                    TranscriptResult('zh', 'test', [TranscriptSegment(0., 2., 'test')], {}),
                    AudioDownloadResult('', 'test', 2., None, 'bilibili', 'BVtest', {}))
            finally:
                with state_lock:
                    active -= 1
    router.NoteGenerator = ControlledGenerator
    original_save = router.save_note_to_file
    def controlled_save(task, result):
        if task.startswith('save-fail-'):
            raise OSError(28, 'injected disk full')
        original_save(task, result)
    router.save_note_to_file = controlled_save
    vector = types.ModuleType('app.services.vector_store')
    vector.VectorStoreManager = lambda: types.SimpleNamespace(index_task=lambda task: None)
    sys.modules['app.services.vector_store'] = vector
    recover_interrupted_tasks(os.environ['NOTE_OUTPUT_DIR'])
    app = FastAPI()
    app.include_router(router.router, prefix='/api')
    @app.get('/control/health')
    async def health():
        return {'ready': True}
    @app.get('/control/stats')
    async def stats():
        with state_lock:
            rss_kib = int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:')))
            return {'counts': dict(counts), 'peak_active': peak_active,
                    'reserved': len(router.task_serial_executor._task_ids),
                    'rss_kib': rss_kib, 'open_fds': len(list(Path('/proc/self/fd').iterdir()))}
    @app.post('/control/release')
    async def unblock():
        release.set()
        return {'ok': True}
    uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[2]), log_level='warning')


@pytest.fixture
def server(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    output = tmp_path/'results'
    output.mkdir()
    env = dict(os.environ, NOTE_OUTPUT_DIR=str(output), TASK_MAX_WORKERS='1', PYTHONPATH=str(ROOT))
    with (tmp_path/'server.log').open('w') as log:
        process = subprocess.Popen([sys.executable, __file__, 'serve', str(port)], cwd=ROOT,
                                   env=env, stdout=log, stderr=subprocess.STDOUT)
        url = f'http://127.0.0.1:{port}'
        try:
            with httpx.Client(base_url=url, trust_env=False, timeout=1) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        pytest.fail((tmp_path/'server.log').read_text())
                    try:
                        if client.get('/control/health').status_code == 200:
                            break
                    except httpx.TransportError:
                        time.sleep(.05)
                else:
                    pytest.fail('isolated server startup timed out')
                def restart():
                    nonlocal process
                    process.kill()
                    process.wait(timeout=3)
                    process = subprocess.Popen([sys.executable, __file__, 'serve', str(port)],
                        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                    for _ in range(100):
                        try:
                            if client.get('/control/health').status_code == 200:
                                return
                        except httpx.TransportError:
                            time.sleep(.05)
                    pytest.fail('restart timed out')
                yield client, output, restart
        finally:
            try:
                httpx.post(url+'/control/release', trust_env=False, timeout=1)
            except httpx.TransportError:
                pass
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def submit(client, task_id):
    response = client.post('/api/generate_note', json={
        'video_url': 'https://www.bilibili.com/video/BV19mYE6ZEM8/',
        'platform': 'bilibili', 'quality': 'medium', 'model_name': 'mock',
        'provider_id': 'mock', 'task_id': task_id,
    })
    assert response.status_code == 200
    return response.json()


def test_status_api_remains_responsive_with_40_waiting_tasks(server, record_property):
    client, output, _ = server
    for i in range(40):
        submit(client, f'queued-{i}')
    # Starlette's default 40 sync-thread tokens must not all be spent waiting
    # on our single-worker executor, starving GET /task_status.
    started = time.monotonic()
    response = client.get('/api/task_status/queued-0', timeout=.75)
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert response.json()['data']['status'] == 'SUMMARIZING'
    assert elapsed < .75
    record_property('status_latency_seconds', elapsed)


if __name__ == '__main__':
    serve()


def wait_terminal(output, ids, timeout=8):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        states = {task: json.loads((output/f'{task}.status.json').read_text()) for task in ids}
        if all(s['status'] in ['SUCCESS', 'FAILED'] for s in states.values()):
            return states
        time.sleep(.02)
    pytest.fail(f'tasks never finished: {states}')


def test_500_mixed_tasks_finish_and_release_queue(server, record_property):
    client, output, _ = server
    started = time.monotonic()
    memory = []
    descriptors = []
    for wave in range(5):
        ids = [f'fail-{wave}-{i}' if i % 5 == 0 else f'normal-{wave}-{i}' for i in range(100)]
        for task in ids:
            submit(client, task)
        client.post('/control/release')
        states = wait_terminal(output, ids)
        assert sum(s['status'] == 'SUCCESS' for s in states.values()) == 80
        assert sum(s['status'] == 'FAILED' for s in states.values()) == 20
        for task in ids:
            data = client.get('/api/task_status/'+task).json()['data']
            if task.startswith('normal-'):
                assert data['result']['markdown'] == 'summary '+task
            else:
                assert 'injected worker failure' in data['message']
        stats = client.get('/control/stats').json()
        assert stats['peak_active'] == 1
        assert stats['reserved'] == 0
        assert all(count == 1 for count in stats['counts'].values())
        memory.append(stats['rss_kib'])
        descriptors.append(stats['open_fds'])
    assert len(stats['counts']) == 500
    assert max(memory)-min(memory) < 64*1024, 'unexpected large RSS growth during short run'
    assert max(descriptors)-min(descriptors) <= 5, 'file descriptors leaked across waves'
    record_property('batch_500_seconds', time.monotonic()-started)
    record_property('rss_kib_by_wave', json.dumps(memory))
    record_property('open_fds_by_wave', json.dumps(descriptors))


def test_40_duplicate_posts_only_execute_once(server):
    client, output, _ = server
    for _ in range(40):
        submit(client, 'duplicate')
    client.post('/control/release')
    assert wait_terminal(output, ['duplicate'])['duplicate']['status'] == 'SUCCESS'
    assert client.get('/control/stats').json()['counts'] == {'duplicate': 1}


def test_save_failure_does_not_publish_success_or_block_next_task(server):
    client, output, _ = server
    submit(client, 'save-fail-1')
    submit(client, 'next')
    client.post('/control/release')
    states = wait_terminal(output, ['save-fail-1', 'next'])
    assert states['save-fail-1']['status'] == 'FAILED'
    assert not (output/'save-fail-1.json').exists()
    assert states['next']['status'] == 'SUCCESS'


def test_killed_process_recovers_running_and_queued_tasks(server):
    client, output, restart = server
    submit(client, 'running')
    submit(client, 'queued')
    for _ in range(100):
        if json.loads((output/'running.status.json').read_text())['status'] == 'SUMMARIZING':
            break
        time.sleep(.01)
    assert json.loads((output/'queued.status.json').read_text())['status'] == 'PENDING'
    restart()
    for task in ['running', 'queued']:
        state = client.get('/api/task_status/'+task).json()['data']
        assert state['status'] == 'FAILED'
        assert '重启' in state['message']
    client.post('/control/release')
    submit(client, 'running')
    assert wait_terminal(output, ['running'])['running']['status'] == 'SUCCESS'


@pytest.mark.parametrize('mode,expected_calls,success', [
    ('rate_limit_then_ok', 2, True), ('server_error_then_ok', 2, True),
    ('unauthorized', 1, False), ('disconnect', 2, False), ('read_stall', 2, False),
])
def test_real_http_failure_budget(tmp_path, monkeypatch, mode, expected_calls, success, record_property):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from app.utils.openai_client import build_openai_client
    from app.gpt.universal_gpt import UniversalGPT
    from openai import APIError
    monkeypatch.setenv('NOTE_OUTPUT_DIR', str(tmp_path))
    monkeypatch.setenv('OPENAI_NO_PROXY', '127.0.0.1')
    monkeypatch.setenv('OPENAI_RETRY_ATTEMPTS', '2')
    monkeypatch.setenv('OPENAI_RETRY_BACKOFF_SECONDS', '.01')
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            requests.append(1)
            if mode == 'disconnect':
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if mode == 'read_stall':
                time.sleep(.35)
            status = 401 if mode == 'unauthorized' else (
                429 if mode == 'rate_limit_then_ok' and len(requests) == 1 else (
                500 if mode == 'server_error_then_ok' and len(requests) == 1 else 200))
            body = json.dumps({'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'},
                                            'finish_reason': 'stop'}]} if status == 200 else
                              {'error': {'message': 'injected error'}}).encode()
            try:
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    endpoint = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: endpoint.serve_forever(poll_interval=.02), daemon=True)
    thread.start()
    try:
        with build_openai_client('local-test-only', f'http://127.0.0.1:{endpoint.server_port}/v1', timeout=.08) as client:
            gpt = UniversalGPT(client, 'mock')
            started = time.monotonic()
            if success:
                assert gpt._chat_completion_create([{'role': 'user', 'content': 'test'}]).choices[0].message.content == 'ok'
            else:
                with pytest.raises(APIError):
                    gpt._chat_completion_create([{'role': 'user', 'content': 'test'}])
            elapsed = time.monotonic()-started
            assert len(requests) == expected_calls
            assert elapsed < 2, 'network failure exceeded the scaled test budget'
            record_property('elapsed_seconds', elapsed)
            record_property('http_attempts', len(requests))
    finally:
        endpoint.shutdown()
        endpoint.server_close()
        thread.join(timeout=1)
