"""Constrain client-controlled paths before they reach filesystem services."""
import re
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException

MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".ts", ".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac", ".wma", ".mpeg", ".mpg"}

PLATFORM_DOMAINS = {
    "bilibili": {"bilibili.com", "b23.tv"},
    "youtube": {"youtube.com", "youtu.be"},
    "douyin": {"douyin.com", "iesdouyin.com"},
    "tiktok": {"douyin.com", "iesdouyin.com"},
    "kuaishou": {"kuaishou.com"},
}


def validate_platform_url(video_url: str, platform: str) -> str:
    parsed = urlparse(video_url)
    host = (parsed.hostname or "").lower()
    domains = PLATFORM_DOMAINS.get(platform, set())
    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(400, "无效的视频链接端口") from None
    if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
            or port not in {None, 80, 443}
            or not any(host == domain or host.endswith("." + domain) for domain in domains)):
        raise HTTPException(400, "视频链接与所选平台不匹配")
    # All supported platforms provide HTTPS. Cookies must not be sent over HTTP.
    return parsed._replace(scheme="https").geturl()


def validate_task_id(task_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
        raise HTTPException(400, "无效的任务标识")
    return task_id


def validate_local_upload(video_url: str, upload_dir: str = "uploads") -> str:
    root = Path(upload_dir).resolve()
    if video_url.startswith("/uploads/"):
        path = (root / video_url[len("/uploads/"):]).resolve()
    else:
        path = Path(video_url).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in MEDIA_SUFFIXES or not path.is_file():
        raise HTTPException(400, "请上传有效的视频或音频文件")
    return str(path)
