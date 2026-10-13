from fastapi import APIRouter, BackgroundTasks, Depends
from app.db.models.users import User
from app.services.auth import get_current_user
from app.services.available_models import require_available_model
from app.utils.request_paths import validate_task_id
from app.services.user_data import require_task, chat_history, append_chat
from app.db import engine as db_module
from app.db.models.user_data import ChatEntry
from app.utils.secrets import customer_result
from pydantic import BaseModel

from app.services.chat_service import chat as chat_service
from app.services.vector_store import VectorStoreManager
from app.utils.logger import get_logger
from app.utils.response import ResponseWrapper as R

logger = get_logger(__name__)

router = APIRouter()

# 索引状态追踪: task_id -> "indexing" | "indexed" | "failed"
_index_status: dict[tuple[int, str], str] = {}


class IndexRequest(BaseModel):
    task_id: str


class ChatMessage(BaseModel):
    role: str
    content: str


class AskRequest(BaseModel):
    task_id: str
    question: str
    history: list[ChatMessage] = []
    provider_id: str
    model_name: str


def _do_index(task_id: str, user_id: int):
    """后台执行索引任务。"""
    try:
        key = (user_id, task_id)
        require_task(task_id, user_id)
        _index_status[key] = "indexing"
        store = VectorStoreManager(user_id=user_id)
        store.index_task(task_id)
        _index_status[key] = "indexed"
        logger.info(f"索引完成: {task_id}")
    except Exception as e:
        _index_status[(user_id, task_id)] = "failed"
        logger.error(f"索引失败: {task_id}, {e}")


@router.post("/chat/index")
def index_task(data: IndexRequest, background_tasks: BackgroundTasks, user: User = Depends(get_current_user)):
    require_task(data.task_id, user.id)
    key = (user.id, data.task_id)
    """触发后台索引，立即返回。"""
    if _index_status.get(key) == "indexing":
        return R.success(msg="正在索引中")

    # 如果已经索引过，直接返回
    store = VectorStoreManager(user_id=user.id)
    if store.is_indexed(data.task_id):
        _index_status[key] = "indexed"
        return R.success(msg="已完成索引")

    _index_status[key] = "indexing"
    background_tasks.add_task(_do_index, data.task_id, user.id)
    return R.success(msg="开始索引")


@router.get("/chat/status")
def chat_status(task_id: str, user: User = Depends(get_current_user)):
    require_task(task_id, user.id)
    key = (user.id, task_id)
    """返回索引状态：idle / indexing / indexed / failed。"""
    try:
        # 优先检查内存状态
        status = _index_status.get(key)
        if status:
            return R.success(data={"status": status, "indexed": status == "indexed"})

        # 内存没有记录，检查持久化
        store = VectorStoreManager(user_id=user.id)
        indexed = store.is_indexed(task_id)
        if indexed:
            _index_status[key] = "indexed"
        return R.success(data={"status": "indexed" if indexed else "idle", "indexed": indexed})
    except Exception as e:
        logger.error(f"查询索引状态失败: {e}")
        return R.success(data={"status": "idle", "indexed": False})


@router.post("/chat/ask")
def ask_question(data: AskRequest, user: User = Depends(get_current_user)):
    """基于笔记内容的 RAG 问答。"""
    validate_task_id(data.task_id)
    if user.role != "admin":
        require_available_model(data.provider_id, data.model_name)
    require_task(data.task_id, user.id)
    try:
        history = chat_history(data.task_id, user.id)
        result = chat_service(
            task_id=data.task_id,
            question=data.question,
            history=history,
            provider_id=data.provider_id,
            model_name=data.model_name,
            user_id=user.id,
        )
        if user.role != 'admin':
            result = customer_result(result)
        append_chat(data.task_id, user.id, [{'role': 'user', 'content': data.question},
                    {'role': 'assistant', 'content': result['answer'], 'sources': result.get('sources', [])}])
        return R.success(data=result)
    except ValueError as e:
        return R.error(msg=str(e) if user.role == "admin" else "问答暂不可用，请重试或联系管理员")
    except Exception as e:
        logger.error(f"Chat 问答失败: {e}", exc_info=True)
        return R.error(msg=f"问答失败: {str(e)}" if user.role == "admin" else "问答失败，请稍后重试")


@router.get('/chat/history')
def get_history(task_id: str, user: User = Depends(get_current_user)):
    return R.success(chat_history(task_id, user.id))


@router.delete('/chat/history')
def clear_history(task_id: str, user: User = Depends(get_current_user)):
    require_task(task_id, user.id)
    with db_module.SessionLocal() as db:
        db.query(ChatEntry).filter_by(user_id=user.id, task_id=task_id).delete()
        db.commit()
    return R.success()
