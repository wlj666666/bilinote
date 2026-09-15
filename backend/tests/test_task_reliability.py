"""Regression coverage for stalled tasks. Run apart from legacy sys.modules stubs."""
import json
from unittest.mock import Mock, patch
import httpx
import pytest
from app.models.audio_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.services import note
from app.routers import note as router
from app.services.task_recovery import recover_interrupted_tasks
from app.services.task_serial_executor import ConcurrentTaskExecutor
from app.utils.openai_client import build_openai_client
from app.gpt.universal_gpt import UniversalGPT


def test_summary_updates_polled_task_but_keeps_checkpoint_key(tmp_path, monkeypatch):
    monkeypatch.setattr(note, 'NOTE_OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(router, 'NOTE_OUTPUT_DIR', str(tmp_path))
    gen = note.NoteGenerator()
    gen._update_status('id_with_underscore', note.TaskStatus.DOWNLOADING)
    def summarize(source):
        state = json.loads(router.get_task_status('id_with_underscore').body)['data']
        assert state['status'] == 'SUMMARIZING'
        assert source.checkpoint_key == 'id_with_underscore_markdown'
        return 'summary'
    gen._summarize_text(AudioDownloadResult('', 'test', 2, None, 'bilibili', 'BVtest', {}),
        TranscriptResult('zh', 'test', [TranscriptSegment(0, 2, 'test')], {}),
        Mock(summarize=summarize), tmp_path/'id_with_underscore_markdown.md', False, False, [], None, None, [])
    assert not (tmp_path/'id_with_underscore_markdown.status.json').exists()


def test_restart_resolves_orphans_and_preserves_finished_tasks(tmp_path):
    states = {'queued': 'PENDING', 'running': 'SUMMARIZING', 'saved': 'SAVING',
              'done': 'SUCCESS', 'failed': 'FAILED', 'running_markdown': 'SUMMARIZING'}
    for task, state in states.items():
        (tmp_path/f'{task}.status.json').write_text(json.dumps({'status': state}))
    (tmp_path/'saved.json').write_text(json.dumps({'markdown': 'ok', 'audio_meta': {'title': 'x'}, 'transcript': {'segments': [1]}}))
    assert recover_interrupted_tasks(tmp_path) == 3
    read = lambda task: json.loads((tmp_path/f'{task}.status.json').read_text())
    assert read('queued')['status'] == read('running')['status'] == 'FAILED'
    assert '重试' in read('queued')['message']
    assert read('saved')['status'] == read('done')['status'] == 'SUCCESS'
    assert read('failed')['status'] == 'FAILED'
    assert read('running_markdown')['status'] == 'SUMMARIZING'
    assert recover_interrupted_tasks(tmp_path) == 0


def test_missing_task_does_not_wait_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(router, 'NOTE_OUTPUT_DIR', str(tmp_path))
    assert json.loads(router.get_task_status('unknown').body)['data']['status'] == 'FAILED'


@pytest.mark.parametrize('proxy', [None, 'http://localhost:7890'])
def test_client_has_bounded_timeouts_and_no_sdk_retries(proxy):
    with patch('app.utils.openai_client.ProxyConfigManager') as manager:
        manager.return_value.get_proxy_url.return_value = proxy
        with build_openai_client('fake-local-key', 'https://mock.invalid/v1') as client:
            assert client.max_retries == 0
            assert client.timeout.connect == 10
            assert client.timeout.write == 30
            assert client.timeout.read == 180


def test_transient_error_uses_only_application_retry_budget(tmp_path, monkeypatch):
    monkeypatch.setenv('NOTE_OUTPUT_DIR', str(tmp_path))
    monkeypatch.setenv('OPENAI_RETRY_ATTEMPTS', '2')
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(500, json={'error': {'message': 'temporary'}})
    with patch('app.utils.openai_client.ProxyConfigManager') as manager:
        manager.return_value.get_proxy_url.return_value = None
        client = build_openai_client('fake-local-key', 'https://mock.invalid/v1')
    with client, httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        test_client = client.with_options(http_client=transport)
        with patch('time.sleep'), pytest.raises(Exception):
            UniversalGPT(test_client, 'test')._chat_completion_create([{'role': 'user', 'content': 'test'}])
    assert len(requests) == 2


@pytest.mark.parametrize('setting', ['OPENAI_NO_PROXY', 'NO_PROXY', 'no_proxy'])
def test_explicit_proxy_respects_host_exclusions(monkeypatch, setting):
    for key in ['OPENAI_NO_PROXY', 'NO_PROXY', 'no_proxy']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(setting, 'direct.example')
    with patch('app.utils.openai_client.ProxyConfigManager') as manager:
        manager.return_value.get_proxy_url.return_value = 'http://localhost:7890'
        with build_openai_client('fake-local-key', 'https://direct.example/v1') as client:
            assert client._client.trust_env is False
        with build_openai_client('fake-local-key', 'https://notdirect.example/v1') as client:
            assert client._client.trust_env is True


def test_duplicate_reservations_are_released_for_retry():
    executor = ConcurrentTaskExecutor(max_workers=1)
    try:
        assert executor.reserve('test')
        assert not executor.reserve('test')
        executor.release('test')
        assert executor.reserve('test')
    finally:
        executor.shutdown()


def test_invalid_background_task_fails_and_releases_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(note, 'NOTE_OUTPUT_DIR', tmp_path)
    assert router.task_serial_executor.reserve('invalid')
    router.run_note_task('invalid', 'url', 'bilibili', 'medium')
    assert json.loads((tmp_path/'invalid.status.json').read_text())['status'] == 'FAILED'
    assert router.task_serial_executor.reserve('invalid')
    router.task_serial_executor.release('invalid')


def test_success_is_published_only_after_result_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(note, 'NOTE_OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(router, 'NOTE_OUTPUT_DIR', str(tmp_path))
    from app.models.notes_model import NoteResult
    result = NoteResult('summary', TranscriptResult('zh', 'test', [], {}),
                        AudioDownloadResult('', 'test', 2, None, 'bilibili', 'BVtest', {}))
    gen = note.NoteGenerator()
    original_update = gen._update_status
    def update(task, status, *args, **kwargs):
        if status == note.TaskStatus.SUCCESS:
            assert json.loads((tmp_path/f'{task}.json').read_text())['markdown'] == 'summary'
        original_update(task, status, *args, **kwargs)
    gen._update_status = update
    gen.generate = Mock(return_value=result)
    with patch.object(router, 'NoteGenerator', return_value=gen), patch('app.services.vector_store.VectorStoreManager'):
        router.run_note_task('save-test', 'url', 'bilibili', 'medium', model_name='test', provider_id='test')
    assert json.loads(router.get_task_status('save-test').body)['data']['status'] == 'SUCCESS'
