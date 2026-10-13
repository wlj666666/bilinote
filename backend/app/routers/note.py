from typing import Literal
import hashlib
import math
# app/routers/note.py
import json
import os
import uuid
from pathlib import Path
from urllib.parse import quote, unquote
from sqlalchemy import select
from app.db import engine as db_module
from app.db.models.video_tasks import VideoTask
from app.services.storage_scope import current_scope, scoped_notes_dir, task_scope
from app.services.user_data import create_task, require_task, task_notes_dir, save_form, register_file, require_file, read_json
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from app.db.models.users import User
from app.services.auth import get_current_user
from app.services.available_models import require_available_model
from app.utils.request_paths import MEDIA_SUFFIXES, validate_task_id, validate_local_upload, validate_platform_url
from app.utils.secrets import customer_result
from pydantic import BaseModel, validator, field_validator, model_validator
from dataclasses import asdict

from app.db.video_task_dao import get_task_by_video
from app.enmus.exception import NoteErrorEnum
from app.enmus.note_enums import DownloadQuality
from app.exceptions.note import NoteError
from app.services.note import NoteGenerator, logger
from app.services.task_serial_executor import task_serial_executor
from app.utils.response import ResponseWrapper as R
from app.utils.url_parser import extract_video_id, normalize_video_url
from app.validators.video_url_validator import is_supported_video_url
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
import httpx
from app.enmus.task_status_enums import TaskStatus

# from app.services.downloader import download_raw_audio
# from app.services.whisperer import transcribe_audio

router = APIRouter()


class RecordRequest(BaseModel):
    task_id: str | None = None
    video_id: str | None = None
    platform: str | None = None


class VideoRequest(BaseModel):
    video_url: str
    platform: str
    quality: DownloadQuality
    screenshot: Optional[bool] = False
    link: Optional[bool] = False
    model_name: str
    provider_id: str
    task_id: Optional[str] = None
    format: Optional[list] = []
    style: str = None
    extras: Optional[str]=None
    text_extraction_method: Literal["asr", "ocr"] = "asr"
    video_understanding: Optional[bool] = False
    video_interval: Optional[int] = 0
    grid_size: Optional[list] = []
    # 客户端（如浏览器插件）已经在用户浏览器里抓到字幕，直接传给后端复用，
    # 跳过 download_subtitles 和音频转写。形如：
    #   {"language": "zh", "full_text": "...", "segments": [{"start","end","text"}, ...]}
    prefetched_transcript: Optional[dict] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_url(cls, data):
        # 稍后再看/收藏夹/带追踪参数的 B 站链接先规范化成标准 /video/BVxxx 形式，
        # 后续校验和 yt-dlp 下载拿到的都是干净链接
        if isinstance(data, dict) and data.get("platform") == "bilibili" and data.get("video_url"):
            data["video_url"] = normalize_video_url(str(data["video_url"]))
        return data

    @field_validator("video_url")
    def validate_supported_url(cls, v):
        url = str(v)
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            # 是网络链接，继续用原有平台校验
            if not is_supported_video_url(url):
                raise NoteError(code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                                message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message)

        return v


NOTE_OUTPUT_DIR = os.getenv("NOTE_OUTPUT_DIR", "note_results")
UPLOAD_DIR = os.getenv('UPLOAD_ROOT', 'uploads')


def save_note_to_file(task_id: str, note):
    directory = scoped_notes_dir(NOTE_OUTPUT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    from app.services.hard_subtitle_extractor import atomic_json
    previous = read_json(directory / f'{task_id}.json')
    atomic_json(directory / f"{task_id}.json", asdict(note))
    scope = current_scope.get()
    if scope:
        from app.services.user_data import save_generated_versions
        save_generated_versions(task_id, scope.user_id, note.markdown, previous.get('markdown'))


def _persist_prefetched_transcript(task_id: str, transcript: dict, video_url: str = "", platform: str = "bilibili") -> None:
    """把客户端预取的字幕写到 NoteGenerator 期望的转写缓存文件里。

    NoteGenerator.generate 会优先读 <task_id>_transcript.json，命中即跳过 download_subtitles
    与音频转写流程。要求字段：language(可空)/full_text/segments[{start,end,text}]
    """
    segments = transcript.get("segments") or []
    cleaned_segments = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        start, end = float(s.get("start", 0)), float(s.get("end", 0))
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("prefetched_transcript 时间戳无效")
        cleaned_segments.append({
            "start": start,
            "end": end,
            "text": text,
        })
    if not cleaned_segments:
        raise ValueError("prefetched_transcript 没有可用的 segments")

    full_text = transcript.get("full_text") or " ".join(s["text"] for s in cleaned_segments)
    payload = {
        "language": transcript.get("language") or "zh",
        "full_text": full_text,
        "segments": cleaned_segments,
        "raw": {"source": "client_prefetched", "input_key": hashlib.sha256(json.dumps({"url": str(video_url), "platform": platform}, sort_keys=True).encode()).hexdigest()},
    }

    directory = scoped_notes_dir(NOTE_OUTPUT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{task_id}_transcript.json"
    from app.services.hard_subtitle_extractor import atomic_json
    atomic_json(target, payload)
    logger.info(f"已写入客户端预取字幕缓存: {target} ({len(cleaned_segments)} 段)")


def run_note_task(task_id: str, video_url: str, platform: str, quality: DownloadQuality,
                  link: bool = False, screenshot: bool = False, model_name: str = None, provider_id: str = None,
                  _format: list = None, style: str = None, extras: str = None, video_understanding: bool = False,
                  video_interval=0, grid_size=[], text_extraction_method="asr", user_id: int | None = None
                  ):
    args = (task_id, video_url, platform, quality, link, screenshot, model_name, provider_id, _format, style, extras,
            video_understanding, video_interval, grid_size, text_extraction_method)
    if user_id is None:
        return _run_note_task(*args)
    try:
        task = require_task(task_id, user_id)
    except Exception:
        task_serial_executor.release(task_id)
        raise
    with task_scope(user_id, task_id, task_notes_dir(task, NOTE_OUTPUT_DIR)):
        return _run_note_task(*args)


def _run_note_task(task_id, video_url, platform, quality, link=False, screenshot=False, model_name=None,
                   provider_id=None, _format=None, style=None, extras=None, video_understanding=False,
                   video_interval=0, grid_size=None, text_extraction_method='asr'):

    def _execute_note_task():
        return NoteGenerator().generate(
            video_url=video_url,
            platform=platform,
            quality=quality,
            task_id=task_id,
            model_name=model_name,
            provider_id=provider_id,
            link=link,
            _format=_format,
            style=style,
            extras=extras,
            screenshot=screenshot,
            video_understanding=video_understanding,
            video_interval=video_interval,
            grid_size=grid_size,
            text_extraction_method=text_extraction_method,
        )

    logger.info(f"任务进入执行队列 (task_id={task_id})")
    try:
        if not model_name or not provider_id:
            raise ValueError("请选择模型和提供者")
        # Already running inside our executor; never occupy a FastAPI thread
        # waiting on another worker (or submit recursively to the same pool).
        note = _execute_note_task()
        if not note or not note.markdown:
            logger.warning(f"任务 {task_id} 执行失败，跳过保存")
            return
        scope = current_scope.get()
        if scope:
            require_task(task_id, scope.user_id)
        save_note_to_file(task_id, note)
        NoteGenerator()._update_status(task_id, TaskStatus.SUCCESS)
        logger.info(f"笔记已保存: {task_id}")
    except Exception as exc:
        logger.exception("任务执行或保存失败 task_id=%s", task_id)
        NoteGenerator()._handle_exception(task_id, exc)
        return
    finally:
        task_serial_executor.release(task_id)

    # 自动建立向量索引（用于 AI 问答），失败不影响笔记生成
    try:
        from app.services.vector_store import VectorStoreManager
        VectorStoreManager().index_task(task_id)
    except Exception as e:
        logger.warning(f"向量索引失败（不影响笔记）: {e}")


@router.post('/delete_task')
def delete_task(data: RecordRequest, user: User = Depends(get_current_user)):
    task_id = data.task_id
    if not task_id:
        with db_module.SessionLocal() as db:
            task = db.scalar(select(VideoTask).where(VideoTask.video_id == data.video_id, VideoTask.platform == data.platform,
                              VideoTask.user_id == user.id, VideoTask.deleted.is_(False)).order_by(VideoTask.id.desc()))
            if task is None:
                raise HTTPException(404, '任务不存在')
            task_id = task.task_id
    validate_task_id(task_id)
    # Reserve atomically against retries while purging this task.
    with db_module.SessionLocal() as db:
        from app.db.models.user_data import DeletedTask
        task = db.scalar(select(VideoTask).where(VideoTask.task_id == task_id, VideoTask.user_id == user.id))
        marker = db.get(DeletedTask, task_id)
        if task is None and (marker is None or marker.user_id != user.id):
            raise HTTPException(404, '任务不存在')
    if task_serial_executor.is_reserved(task_id) or not task_serial_executor.reserve(task_id):
        raise HTTPException(409, '任务正在处理，请结束后再删除')
    try:
        from app.services.task_deletion import purge_task
        purge_task(task_id, user.id, NOTE_OUTPUT_DIR, UPLOAD_DIR)
    except HTTPException:
        raise
    except Exception:
        logger.exception('清理任务失败 task_id=%s', task_id)
        raise HTTPException(500, '任务清理未完成，请重试删除或联系管理员') from None
    finally:
        task_serial_executor.release(task_id)
    return R.success(msg='删除成功')


@router.post("/upload")
async def upload(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    root = Path(UPLOAD_DIR).resolve()
    user_directory = root / 'users' / str(user.id)
    user_directory.mkdir(parents=True, exist_ok=True)
    filename = Path((file.filename or "").replace("\\", "/")).name
    if Path(filename).suffix.lower() not in MEDIA_SUFFIXES:
        raise HTTPException(400, "仅支持上传视频或音频文件")
    # User filenames must never overwrite config, source files, or existing uploads.
    filename = f"{uuid.uuid4().hex}_{filename}"
    file_location = user_directory / filename
    with open(file_location, "xb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    relative = register_file('uploads', file_location, user.id, None, root)
    return R.success({"url": f"/uploads/{quote(relative, safe='/')}"})


@router.post("/generate_note")
def generate_note(data: VideoRequest, user: User = Depends(get_current_user)):
    if user.role != "admin":
        require_available_model(data.provider_id, data.model_name)
        if data.platform != 'local':
            data.video_url = validate_platform_url(data.video_url, data.platform)
    if data.platform == 'local':
        from app.routers.files import local_file_reference
        reference = local_file_reference(data.video_url)
        if reference and reference[0] == 'uploads':
            source = require_file('uploads', reference[1], user.id, Path(UPLOAD_DIR))
        else:
            source = Path(data.video_url).resolve()
            root = Path(UPLOAD_DIR).resolve()
            if not source.is_relative_to(root):
                raise HTTPException(400, '请先上传视频或音频文件')
            source = require_file('uploads', source.relative_to(root).as_posix(), user.id, root)
        # Keep the client URL in form data, pass only the verified local path to workers.
        local_source = str(source)
    else:
        local_source = data.video_url
    if data.task_id:
        validate_task_id(data.task_id)
        require_task(data.task_id, user.id)
    reserved = False
    try:
        video_id = extract_video_id(data.video_url, data.platform)
        # if not video_id:
        #     raise HTTPException(status_code=400, detail="无法提取视频 ID")
        # existing = get_task_by_video(video_id, data.platform)
        # if existing:
        #     return R.error(
        #         msg='笔记已生成，请勿重复发起',
        #
        #     )
        if data.task_id:
            # 如果传了task_id，说明是重试！
            task_id = data.task_id
            logger.info(f"重试模式，复用已有 task_id={task_id}")
        else:
            # 正常新建任务
            task_id = str(uuid.uuid4())
            create_task(task_id, user.id, data.model_dump(exclude={'prefetched_transcript'}, mode='json'))

        if not task_serial_executor.reserve(task_id):
            return R.success({"task_id": task_id}, msg="任务已在队列中，无需重复提交")
        reserved = True
        save_form(task_id, user.id, data.model_dump(exclude={'prefetched_transcript'}, mode='json'))
        task = require_task(task_id, user.id)
        with task_scope(user.id, task_id, task_notes_dir(task, NOTE_OUTPUT_DIR)):
            NoteGenerator()._update_status(task_id, TaskStatus.PENDING, '已进入队列，等待处理')

        # 客户端已经抓好字幕的话，写到转写缓存文件，NoteGenerator 的 cache-hit 逻辑会直接用上
        if data.prefetched_transcript:
            try:
                with task_scope(user.id, task_id, task_notes_dir(task, NOTE_OUTPUT_DIR)):
                    _persist_prefetched_transcript(task_id, data.prefetched_transcript, local_source, data.platform)
            except Exception as e:
                logger.warning(f"写入预取字幕失败 (task_id={task_id}): {e}")

        task_serial_executor.submit(run_note_task, task_id, local_source, data.platform, data.quality, data.link,
                                  data.screenshot, data.model_name, data.provider_id, data.format, data.style,
                                  data.extras, data.video_understanding, data.video_interval, data.grid_size, data.text_extraction_method, user_id=user.id)
        return R.success({"task_id": task_id})
    except Exception as e:
        if reserved:
            task_serial_executor.release(task_id)
            with task_scope(user.id, task_id, task_notes_dir(require_task(task_id, user.id), NOTE_OUTPUT_DIR)):
                NoteGenerator()._handle_exception(task_id, e)
        raise HTTPException(status_code=500, detail=str(e) if user.role == "admin" else "任务提交失败，请稍后重试")


@router.get("/task_status/{task_id}")
def get_task_status(task_id: str, user: User = Depends(get_current_user)):
    task = require_task(task_id, user.id)
    directory = task_notes_dir(task, NOTE_OUTPUT_DIR)
    status_path = directory / f'{task_id}.status.json'
    result_path = directory / f'{task_id}.json'
    if task.display_data and not result_path.exists() and not status_path.exists():
        from app.services.user_data import display_task
        display = display_task(task, NOTE_OUTPUT_DIR, user.role == 'admin')
        markdown = display['markdown']
        if isinstance(markdown, list):
            markdown = markdown[0].get('content', '') if markdown else ''
        return R.success({'status': display['status'], 'task_id': task_id, 'message': display['message'],
                          'result': {'markdown': markdown, 'transcript': display['transcript'], 'audio_meta': display['audioMeta']}})

    # 优先读状态文件
    if os.path.exists(status_path):
        with open(status_path, "r", encoding="utf-8") as f:
            status_content = json.load(f)

        status = status_content.get("status")
        message = status_content.get("message", "")
        if user.role != "admin":
            message = customer_result(message)

        if status == TaskStatus.SUCCESS.value:
            # 成功状态的话，继续读取最终笔记内容
            if os.path.exists(result_path):
                with open(result_path, "r", encoding="utf-8") as rf:
                    result_content = json.load(rf)
                if user.role != "admin":
                    result_content = customer_result(result_content)
                return R.success({
                    "status": status,
                    "result": result_content,
                    "message": message,
                    "task_id": task_id
                })
            else:
                # 理论上不会出现，保险处理
                return R.success({
                    "status": TaskStatus.FAILED.value,
                    "message": "结果文件未找到，请重试生成",
                    "task_id": task_id
                })

        if status == TaskStatus.FAILED.value:
            if user.role != "admin":
                message = "任务处理失败，请重试或联系管理员"
            return R.success({"status": TaskStatus.FAILED.value, "message": message or "任务失败", "task_id": task_id})

        # 处理中状态
        return R.success({
            "status": status,
            "message": message,
            "task_id": task_id
        })

    # 没有状态文件，但有结果
    if os.path.exists(result_path):
        with open(result_path, "r", encoding="utf-8") as f:
            result_content = json.load(f)
        if user.role != "admin":
            result_content = customer_result(result_content)
        return R.success({
            "status": TaskStatus.SUCCESS.value,
            "result": result_content,
            "task_id": task_id
        })

    # A missing task is not queued; reporting PENDING here spins forever.
    return R.success({
        "status": TaskStatus.FAILED.value,
        "message": "任务记录不存在，请重新生成",
        "task_id": task_id
    })


@router.get("/image_proxy")
async def image_proxy(request: Request, url: str, user: User = Depends(get_current_user)):
    from app.routers.files import local_file_reference, private_file
    reference = local_file_reference(url)
    if reference:
        return private_file(*reference, user)
    headers = {
        "Referer": "https://www.bilibili.com/",
        "User-Agent": request.headers.get("User-Agent", ""),
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="图片获取失败")

            content_type = resp.headers.get("Content-Type", "image/jpeg")
            if content_type.split(";", 1)[0].lower() not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}:
                raise HTTPException(400, "仅支持代理图片")
            return StreamingResponse(
                resp.aiter_bytes(),
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=86400",  #  缓存一天
                    "Content-Type": content_type,
                }
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="图片获取失败") from None
