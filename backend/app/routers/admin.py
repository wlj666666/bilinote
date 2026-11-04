"""Explicit administrator endpoints; customer ownership rules remain unchanged."""
import copy
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import engine as db_module
from app.db.models.task_jobs import TaskJob
from app.db.models.video_tasks import VideoTask
from app.services import admin as service
from app.services.auth import require_admin
from app.services.task_queue import cancel, enqueue
from app.utils.response import ResponseWrapper as R

router = APIRouter(prefix='/admin', dependencies=[Depends(require_admin)])


@router.get('/overview')
def overview():
    return R.success(service.overview())


@router.get('/resources')
def resources():
    from app.services.admin_resources import resource_snapshot, service_snapshot
    return R.success({'resources': resource_snapshot(), 'services': service_snapshot()})


@router.get('/tasks')
def tasks(kind: Literal['note', 'chat'] = 'note', user_id: int | None = Query(None, ge=1),
          state: str | None = Query(None, max_length=40), q: str = Query('', max_length=128),
          page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return R.success(service.task_list(kind, user_id, state, q.strip(), page, page_size))


@router.get('/tasks/{kind}/{task_id}')
def task_detail(kind: Literal['note', 'chat'], task_id: str):
    return R.success(service.task_detail(kind, task_id))


@router.post('/tasks/{task_id}/cancel')
def cancel_task(task_id: str):
    with db_module.SessionLocal() as db:
        from sqlalchemy import select
        task = db.scalar(select(VideoTask).where(VideoTask.task_id == task_id, VideoTask.deleted.is_(False)))
        if task is None:
            raise HTTPException(404, '任务不存在')
        owner_id = task.user_id
    cancel(task_id, owner_id)
    return R.success({'task_id': task_id}, msg='排队已取消')


class RetryRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=74)
    expected_updated_at: float = Field(allow_inf_nan=False)


@router.post('/tasks/{task_id}/retry')
def retry_task(task_id: str, data: RetryRequest):
    from app.services.available_models import require_available_model
    with db_module.SessionLocal() as db:
        job = db.get(TaskJob, task_id)
        if job is None:
            raise HTTPException(409, '旧任务没有可复用的执行参数，请由原用户重新提交')
        owner_id = job.user_id
        payload = copy.deepcopy(service.object_json(job.payload))
    form = payload.get('form')
    if not isinstance(form, dict) or not payload.get('transcriber'):
        raise HTTPException(409, '任务参数不完整，请由原用户重新提交')
    require_available_model(form.get('provider_id'), form.get('model_name'))
    # Both the status and version are checked inside admission's write transaction.
    result = enqueue(owner_id, payload, task_id=task_id, request_id='admin:' + data.request_id,
                     expected_state='FAILED', expected_updated_at=data.expected_updated_at)
    return R.success({'task_id': result}, msg='已按原用户重新排队')


@router.get('/users')
def users(q: str = Query('', max_length=64), enabled: bool | None = None,
          page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return R.success(service.users_page(q.strip(), enabled, page, page_size))
