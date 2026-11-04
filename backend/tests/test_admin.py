"""Admin API regression tests against real temporary SQLite storage and real auth."""
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.db import engine as db_module
from app.db.init_db import init_db
from app.db.models.chat_jobs import ChatJob
from app.db.models.models import Model
from app.db.models.providers import Provider
from app.db.models.task_jobs import TaskJob
from app.db.models.users import AuthSession, User
from app.db.models.video_tasks import VideoTask
from app.routers import admin, auth
from app.services import admin as service
from app.services import admin_resources
from app.services.auth import token_digest, verify_password
from app.services.task_limits import TaskLimits
from app.services.task_queue import claim
from app.utils.secrets import register_secret


class AdminAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        engine = create_engine('sqlite:///' + str(self.root / 'test.db'), connect_args={'check_same_thread': False})
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        self.addCleanup(engine.dispose)
        for name, value in [('engine', engine), ('SessionLocal', sessions)]:
            patcher = patch.object(db_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {'NOTE_OUTPUT_DIR': str(self.root / 'notes')})
        env.start()
        self.addCleanup(env.stop)
        init_db()
        with sessions() as db:
            for user_id, name, role in [(1, 'admin', 'admin'), (2, 'alice', 'user'), (3, 'bobby', 'user')]:
                db.add(User(id=user_id, username=name, password_hash='unused', role=role, enabled=True))
                db.add(AuthSession(token_hash=token_digest(name + '-test-session'), user_id=user_id, expires_at=time.time() + 3600))
            db.add(Provider(id='provider', name='Test', api_key='sk-admin-fixture-secret', logo='custom',
                            type='custom', base_url='https://example.invalid/v1', enabled=1))
            db.add(Model(provider_id='provider', model_name='test-model'))
            db.commit()
        register_secret('sk-admin-fixture-secret')
        app = FastAPI()
        app.include_router(admin.router, prefix='/api')
        app.include_router(auth.router, prefix='/api')
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer admin-test-session'}

    def task(self, task_id, owner=2, state='FAILED', phase=None, deleted=False):
        payload = {'source': 'https://www.bilibili.com/video/BV1234',
                   'form': {'video_url': 'https://www.bilibili.com/video/BV1234', 'platform': 'bilibili',
                            'quality': 'medium', 'provider_id': 'provider', 'model_name': 'test-model'},
                   'transcriber': {'transcriber_type': 'fast-whisper', 'whisper_model_size': 'tiny'}}
        with db_module.SessionLocal() as db:
            db.add(VideoTask(task_id=task_id, user_id=owner, platform='bilibili', video_id='BV1234',
                             title='真实测试视频 ' + task_id, deleted=deleted, storage_relpath=f'users/{owner}',
                             form_data=json.dumps(payload['form']), display_data=json.dumps({'markdown': 'PRIVATE-NOTE-BODY'})))
            db.add(TaskJob(task_id=task_id, user_id=owner, payload=json.dumps(payload), state=state,
                           phase=phase or state, created_at=100, started_at=105 if state != 'QUEUED' else None,
                           finished_at=120 if state in {'SUCCESS', 'FAILED'} else None, updated_at=120,
                           error_detail='Bearer abc-secret sk-admin-fixture-secret', error_type='TestError'))
            db.commit()

    def get(self, path):
        response = self.client.get('/api/admin/' + path, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['data']

    def test_admin_read_and_write_endpoints_reject_anonymous_and_customers(self):
        self.task('failed')
        paths = ['overview', 'resources', 'tasks', 'tasks/note/failed', 'users']
        for path in paths:
            self.assertEqual(self.client.get('/api/admin/' + path).status_code, 401)
            self.assertEqual(self.client.get('/api/admin/' + path, headers={'Authorization': 'Bearer alice-test-session'}).status_code, 403)
        for operation in ['cancel', 'retry']:
            body = {'request_id': 'unauthorized', 'expected_updated_at': 120}
            self.assertEqual(self.client.post(f'/api/admin/tasks/failed/{operation}', json=body,
                headers={'Authorization': 'Bearer alice-test-session'}).status_code, 403)

    def test_overview_matches_filtered_lists_and_excludes_deleted_tasks(self):
        for task_id, state, phase in [('done', 'SUCCESS', 'SUCCESS'), ('bad', 'FAILED', 'FAILED'),
                                     ('wait', 'RUNNING', 'WAITING_RESOURCE'), ('queue', 'QUEUED', 'PENDING')]:
            self.task(task_id, state=state, phase=phase)
        self.task('deleted', state='SUCCESS', deleted=True)
        overview = self.get('overview')
        self.assertEqual(overview['user_count'], 3)
        self.assertEqual(overview['note_total'], 4)
        for state, count in overview['note_states'].items():
            self.assertEqual(self.get('tasks?state=' + state)['total'], count)
        self.assertEqual(self.get('tasks?state=WAITING_RESOURCE')['items'][0]['id'], 'wait')
        self.assertEqual(self.get('tasks?page_size=2')['total'], 4)
        self.assertEqual(len(self.get('tasks?page_size=2&page=2')['items']), 2)
        self.assertEqual(len(overview['recent_failed_notes']), 1)

    def test_lists_and_details_do_not_expose_note_question_or_credentials(self):
        self.task('video')
        with db_module.SessionLocal() as db:
            db.add(ChatJob(id='chat-one', task_id='video', user_id=2, request_id='chat-one-request', state='failed',
                           payload=json.dumps({'question': 'PRIVATE-QUESTION', 'model_name': 'test-model', 'provider_id': 'provider'}),
                           result=json.dumps({'answer': 'PRIVATE-ANSWER'}), error='sk-admin-fixture-secret', created_at=100, finished_at=110))
            db.commit()
        for path in ['overview', 'tasks', 'tasks/note/video', 'tasks?kind=chat', 'tasks/chat/chat-one', 'users']:
            data = json.dumps(self.get(path))
            for private in ['PRIVATE-NOTE-BODY', 'PRIVATE-QUESTION', 'PRIVATE-ANSWER', 'sk-admin-fixture-secret', 'abc-secret', 'password_hash', 'token_hash']:
                self.assertNotIn(private, data)
        detail = self.get('tasks/chat/chat-one')
        self.assertEqual(detail['elapsed_seconds'], 10)
        self.assertIsNone(detail['started_at'])

    def test_user_filter_search_and_pagination_are_real_database_queries(self):
        self.task('alice-task', owner=2)
        self.task('bobby-task', owner=3)
        self.assertEqual(self.get('tasks?user_id=3')['items'][0]['id'], 'bobby-task')
        self.assertEqual(self.get('tasks?q=alice-task')['total'], 1)
        self.assertEqual(self.get('tasks?q=%25')['total'], 0)
        users = self.get('users?q=alice')
        self.assertEqual(users['total'], 1)
        self.assertEqual(users['items'][0]['task_count'], 1)
        self.assertEqual(self.get('users?page_size=1&page=2')['items'][0]['username'], 'alice')

    def test_cancel_is_idempotent_and_preserves_owner(self):
        self.task('queued', owner=3, state='QUEUED', phase='PENDING')
        for _ in range(2):
            response = self.client.post('/api/admin/tasks/queued/cancel', headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
        with db_module.SessionLocal() as db:
            job = db.get(TaskJob, 'queued')
            self.assertEqual((job.state, job.user_id), ('CANCELLED', 3))
        self.assertEqual(self.get('overview')['note_states']['CANCELLED'], 1)

    def test_cancel_loses_race_to_claim_without_killing_running_task(self):
        self.task('claimed', state='QUEUED', phase='PENDING')
        self.assertIsNotNone(claim())
        response = self.client.post('/api/admin/tasks/claimed/cancel', headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.get('tasks/note/claimed')['state'], 'RUNNING')

    def test_retry_is_atomic_idempotent_and_preserves_owner(self):
        self.task('retry-me', owner=3)
        body = {'request_id': 'same-click', 'expected_updated_at': 120}
        for _ in range(2):
            response = self.client.post('/api/admin/tasks/retry-me/retry', json=body, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
        with db_module.SessionLocal() as db:
            job = db.get(TaskJob, 'retry-me')
            self.assertEqual((job.state, job.user_id, job.attempts), ('QUEUED', 3, 0))
            self.assertEqual(db.scalar(select(func_count()).select_from(TaskJob)), 1)
        response = self.client.post('/api/admin/tasks/retry-me/retry',
            json={**body, 'request_id': 'second-click'}, headers=self.headers)
        self.assertEqual(response.status_code, 409)

    def test_retry_rejects_changed_version_disabled_owner_and_unavailable_model(self):
        self.task('retry')
        url = '/api/admin/tasks/retry/retry'
        body = {'request_id': 'retry-once', 'expected_updated_at': 119}
        self.assertEqual(self.client.post(url, json=body, headers=self.headers).status_code, 409)
        body['expected_updated_at'] = 120
        with db_module.SessionLocal.begin() as db:
            db.get(User, 2).enabled = False
        self.assertEqual(self.client.post(url, json=body, headers=self.headers).status_code, 403)
        with db_module.SessionLocal.begin() as db:
            db.get(User, 2).enabled = True
            db.get(Provider, 'provider').enabled = 0
        self.assertEqual(self.client.post(url, json=body, headers=self.headers).status_code, 403)
        self.assertEqual(self.get('tasks/note/retry')['state'], 'FAILED')

    def test_retry_respects_original_users_queue_limit(self):
        self.task('retry')
        self.task('already-queued', state='QUEUED', phase='PENDING')
        with patch('app.services.task_queue.TaskLimits.from_env', return_value=TaskLimits(user_queued=1)):
            response = self.client.post('/api/admin/tasks/retry/retry',
                json={'request_id': 'full-queue', 'expected_updated_at': 120}, headers=self.headers)
        self.assertEqual(response.status_code, 429)

    def test_concurrent_retry_requests_admit_only_one_execution(self):
        self.task('concurrent')
        barrier = Barrier(2)
        def submit(request_id):
            barrier.wait(timeout=5)
            response = self.client.post('/api/admin/tasks/concurrent/retry',
                json={'request_id': request_id, 'expected_updated_at': 120}, headers=self.headers)
            return response.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, ['concurrent-one', 'concurrent-two']))
        self.assertEqual(sorted(results), [200, 409])
        self.assertIsNotNone(claim())
        self.assertIsNone(claim())

    def test_deleted_tasks_cannot_be_read_or_retried(self):
        self.task('deleted', deleted=True)
        self.assertEqual(self.client.get('/api/admin/tasks/note/deleted', headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post('/api/admin/tasks/deleted/retry',
            json={'request_id': 'deleted', 'expected_updated_at': 120}, headers=self.headers).status_code, 404)

    def test_legacy_metadata_backfill_keeps_counts_and_lists_consistent(self):
        folder = self.root / 'notes' / 'users' / '2'
        folder.mkdir(parents=True)
        (folder / 'legacy.json').write_text(json.dumps({'audio_meta': {'title': '旧版真实标题'}, 'markdown': 'PRIVATE-LEGACY'}))
        with db_module.SessionLocal.begin() as db:
            db.add(VideoTask(task_id='legacy', user_id=2, platform='local', video_id='', storage_relpath='users/2'))
            db.add(VideoTask(task_id='unknown', user_id=2, platform='local', video_id='', storage_relpath='users/2'))
        service.backfill_metadata()
        service.backfill_metadata()
        self.assertEqual(self.get('tasks/note/legacy')['title'], '旧版真实标题')
        self.assertEqual(self.get('tasks/note/legacy')['state'], 'SUCCESS')
        self.assertEqual(self.get('tasks/note/unknown')['state'], 'UNKNOWN')
        self.assertIsNone(self.get('tasks/note/legacy')['elapsed_seconds'])
        self.assertEqual(self.get('overview')['note_states'], {'SUCCESS': 1, 'UNKNOWN': 1})

    def test_legacy_audio_title_does_not_hide_completed_note_without_status_file(self):
        folder = self.root / 'notes' / 'users' / '2'
        folder.mkdir(parents=True)
        (folder / 'old_audio.json').write_text(json.dumps({'title': '真实音频标题'}))
        (folder / 'old.json').write_text(json.dumps({'markdown': 'PRIVATE-LEGACY'}))
        with db_module.SessionLocal.begin() as db:
            db.add(VideoTask(task_id='old', user_id=2, platform='local', video_id='', storage_relpath='users/2'))
        service.backfill_metadata()
        detail = self.get('tasks/note/old')
        self.assertEqual((detail['title'], detail['state']), ('真实音频标题', 'SUCCESS'))

    def test_regenerated_task_is_sorted_by_current_submission(self):
        self.task('older-task')
        self.task('newer-task')
        with db_module.SessionLocal.begin() as db:
            db.get(TaskJob, 'older-task').created_at = 200
        self.assertEqual(self.get('tasks')['items'][0]['id'], 'older-task')

    def test_user_disable_and_password_reset_revoke_real_sessions(self):
        self.task('queued', state='QUEUED', phase='PENDING')
        url = '/api/auth/users/2'
        response = self.client.patch(url, json={'enabled': False}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/auth/me', headers={'Authorization': 'Bearer alice-test-session'}).status_code, 401)
        self.assertIsNone(claim())
        self.assertEqual(self.get('tasks/note/queued')['state'], 'QUEUED')
        self.client.patch(url, json={'enabled': True}, headers=self.headers)
        with db_module.SessionLocal.begin() as db:
            db.add(AuthSession(token_hash=token_digest('reset-session'), user_id=2, expires_at=time.time() + 100))
        password = 'replacement-password-2026'
        response = self.client.patch(url, json={'password': password}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/auth/me', headers={'Authorization': 'Bearer reset-session'}).status_code, 401)
        with db_module.SessionLocal() as db:
            self.assertTrue(verify_password(password, db.get(User, 2).password_hash))
        self.assertIsNotNone(claim())

    def test_new_user_and_invalid_filters(self):
        response = self.client.post('/api/auth/users', headers=self.headers,
                                    json={'username': 'new-user', 'password': 'new-password-2026', 'role': 'admin'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get('users?q=new-user')['items'][0]['role'], 'user')
        for path in ['tasks?page=0', 'tasks?page_size=101', 'tasks?kind=bad', 'users?page_size=101']:
            self.assertEqual(self.client.get('/api/admin/' + path, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.get('/api/admin/tasks?state=INVALID', headers=self.headers).status_code, 400)

    def test_metadata_schema_upgrade_is_idempotent(self):
        self.task('before-upgrade')
        with db_module.engine.begin() as connection:
            connection.execute(text('ALTER TABLE video_tasks DROP COLUMN title'))
            connection.execute(text('ALTER TABLE video_tasks DROP COLUMN legacy_status'))
        init_db()
        init_db()
        self.task('after-upgrade')
        self.assertEqual(self.get('tasks')['total'], 2)
        before = self.get('tasks/note/before-upgrade')
        self.assertEqual((before['state'], before['user_id']), ('FAILED', 2))


def func_count():
    from sqlalchemy import func
    return func.count()


class AdminResourceTests(unittest.TestCase):
    def test_unavailable_gpu_is_not_reported_as_zero_utilization(self):
        with patch.object(admin_resources.shutil, 'which', return_value='nvidia-smi'), patch.object(
                admin_resources.subprocess, 'run', return_value=SimpleNamespace(stdout='Test GPU, [N/A], 0, 8192\n')):
            gpu = admin_resources.gpu_probe()
        self.assertIsNone(gpu['devices'][0]['percent'])
        self.assertEqual(gpu['devices'][0]['used_bytes'], 0)

    def test_gpu_failure_is_isolated_from_other_real_probes(self):
        with patch.object(admin_resources, '_cached', None), patch.object(admin_resources, 'gpu_probe', side_effect=OSError):
            snapshot = admin_resources.resource_snapshot()
        self.assertEqual(snapshot['gpu']['status'], 'unsupported')
        self.assertEqual(snapshot['disk']['status'], 'ok')
        self.assertGreater(snapshot['disk']['total_bytes'], 0)
        self.assertGreater(snapshot['observed_at'], 0)

    def test_cgroup_memory_limit_and_usage_are_reported_together(self):
        def content(path):
            if str(path) == '/proc/meminfo':
                return 'MemTotal: 8192 kB\nMemAvailable: 4096 kB\n'
            if str(path).endswith('memory.max'):
                return '4194304'
            if str(path).endswith('memory.current'):
                return '1048576'
            raise OSError()
        with patch.object(Path, 'read_text', content):
            memory = admin_resources.linux_memory()
        self.assertEqual(memory['scope'], 'container')
        self.assertEqual(memory['percent'], 25)
        self.assertEqual(memory['total_bytes'], 4194304)

    def test_alive_but_stalled_or_failing_scheduler_is_unhealthy(self):
        scheduler = SimpleNamespace(thread=Mock(is_alive=lambda: True), inference=Mock(poll=lambda: None),
                                    last_heartbeat_at=time.time() - 60, last_error_at=None, last_cycle_ok_at=None)
        with patch('app.services.task_scheduler.task_scheduler', scheduler):
            self.assertEqual(admin_resources.service_snapshot()['scheduler']['status'], 'error')
            scheduler.last_heartbeat_at = time.time()
            scheduler.last_error_at = time.time()
            self.assertEqual(admin_resources.service_snapshot()['scheduler']['status'], 'error')
            scheduler.last_cycle_ok_at = time.time() + .01
            self.assertEqual(admin_resources.service_snapshot()['scheduler']['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
