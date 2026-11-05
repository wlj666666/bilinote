"""Exercise the worker and model retry budgets together without real API calls."""
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from openai import APITimeoutError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import engine as db_module
from app.db.init_db import Base
from app.db.models.task_jobs import TaskJob
from app.db.models.users import User
from app.db.models.video_tasks import VideoTask
from app.gpt.universal_gpt import UniversalGPT
from app.routers import note
from app.services import task_context, task_worker


class TaskRetryTests(unittest.TestCase):
    def setUp(self):
        for context in (task_context.attempt, task_context.transcriber_snapshot, task_context.summary_revision):
            token = context.set(None)
            self.addCleanup(context.reset, token)
        self.gpt = UniversalGPT.__new__(UniversalGPT)
        self.gpt.model = 'test-model'
        self.gpt._max_retry_attempts = 2
        self.gpt._retry_base_backoff = 1
        self.result = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='笔记'))])
        self.timeout = APITimeoutError(request=httpx.Request('POST', 'https://example.invalid'))
        self.gpt._do_create = Mock(side_effect=self.timeout)
        self.messages = [{'role': 'user', 'content': '测试字幕'}]

    def test_standalone_model_keeps_its_retry_budget(self):
        self.gpt._do_create.side_effect = [self.timeout, self.result]
        with patch.object(task_context, 'retry_wait') as wait:
            self.assertIs(self.gpt._chat_completion_create(self.messages), self.result)
        self.assertEqual(self.gpt._do_create.call_count, 2)
        wait.assert_called_once_with(1)

    def test_managed_model_keeps_its_retry_budget(self):
        task_context.attempt.set(('task', 'token'))
        with patch.object(task_context, 'retry_wait') as wait:
            with self.assertRaises(APITimeoutError):
                self.gpt._chat_completion_create(self.messages)
        self.assertEqual(self.gpt._do_create.call_count, 2)
        wait.assert_called_once_with(1)

    def run_worker(self, outcomes):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = create_engine('sqlite:///' + str(Path(temporary.name) / 'test.db'))
        self.addCleanup(database.dispose)
        Base.metadata.create_all(database)
        sessions = sessionmaker(bind=database, expire_on_commit=False)
        task_id = str(uuid.uuid4())
        payload = {'source': 'https://www.bilibili.com/video/BV1234',
                   'form': {'platform': 'bilibili', 'quality': 'medium',
                            'model_name': 'test-model', 'provider_id': 'test'},
                   'transcriber': {'transcriber_type': 'fast-whisper', 'whisper_model_size': 'tiny'}}
        with sessions.begin() as db:
            db.add(User(id=1, username='tester', password_hash='unused', role='user', enabled=True))
            db.add(VideoTask(task_id=task_id, user_id=1, platform='bilibili', video_id='BV1234'))
            db.add(TaskJob(task_id=task_id, user_id=1, payload=json.dumps(payload), state='RUNNING',
                           phase='SUMMARIZING', attempt_token='token', attempts=1))
        self.gpt._do_create.side_effect = outcomes
        run = Mock(side_effect=lambda *args, **kwargs: self.gpt._chat_completion_create(self.messages))
        with patch.dict(os.environ, {'TASK_JOB_ID': task_id, 'TASK_ATTEMPT_TOKEN': 'token',
                                     'TASK_PARENT_PID': '1', 'TASK_RETRY_ATTEMPTS': '2'}), \
             patch.object(db_module, 'SessionLocal', sessions), \
             patch.object(task_worker, 'watch_parent'), \
             patch.object(task_worker.threading, 'Thread'), \
             patch.object(task_worker.threading, 'Event', return_value=Mock()), \
             patch.object(task_context, 'retry_wait'), \
             patch.object(note, 'run_note_task', run):
            try:
                task_worker.execute()
            except APITimeoutError:
                pass
        with sessions() as db:
            job = db.get(TaskJob, task_id)
            db.expunge(job)
        return job, run

    def test_two_task_attempts_preserve_four_request_opportunities(self):
        job, run = self.run_worker([self.timeout] * 4)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(self.gpt._do_create.call_count, 4)
        self.assertEqual((job.state, job.attempts, job.error_type), ('FAILED', 2, 'APITimeoutError'))

    def test_recovered_task_clears_current_error_and_keeps_attempt_count(self):
        # Reproduce the reported task: three timeouts, then a successful fourth request.
        job, run = self.run_worker([self.timeout] * 3 + [self.result])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(self.gpt._do_create.call_count, 4)
        self.assertEqual((job.state, job.attempts), ('SUCCESS', 2))
        self.assertIsNone(job.error_type)
        self.assertIsNone(job.error_detail)
        self.assertIsNone(job.next_retry_at)


if __name__ == '__main__':
    unittest.main()
