"""统一构造 OpenAI 兼容客户端：注入全局代理 + 校验 api_key。

为什么要这一层：
  - 代理：openai SDK 默认只认进程级 HTTP_PROXY 环境变量，桌面端用户在 UI 里
    填的代理需要显式塞进 httpx.Client 才生效。
  - api_key 校验：空 key 会让 httpx 拼出非法 header `Bearer `，抛出
    `httpx.LocalProtocolError: Illegal header value b'Bearer '` 这种天书报错。
    在入口挡掉，给用户「xxx 的 API Key 未配置」这种能看懂的提示。
"""
from typing import Optional
import os
import httpx
from urllib.parse import urlsplit
from urllib.request import proxy_bypass_environment

from openai import OpenAI

from app.services.proxy_config_manager import ProxyConfigManager
from app.utils.logger import get_logger

logger = get_logger(__name__)


def build_openai_client(
    api_key: Optional[str],
    base_url: Optional[str],
    *,
    key_label: str = "API Key",
    timeout: Optional[float] = None,
) -> OpenAI:
    """构造 OpenAI 客户端。api_key 为空直接抛清晰错误；代理已配置则注入。

    key_label 用于错误提示，例如 "Groq 的 API Key" / "OpenAI 供应商的 API Key"。
    """
    if not api_key or not str(api_key).strip():
        raise ValueError(f"{key_label} 未配置，请先在「设置」里填写后再使用")

    request_timeout = timeout if timeout is not None else httpx.Timeout(
        connect=float(os.getenv("OPENAI_CONNECT_TIMEOUT", "10")),
        write=float(os.getenv("OPENAI_WRITE_TIMEOUT", "30")),
        read=float(os.getenv("OPENAI_READ_TIMEOUT", "180")),
        pool=10.0,
    )
    # UniversalGPT owns retries. SDK retries here used to multiply 3 attempts into 9.
    kwargs = {"api_key": str(api_key).strip(), "base_url": base_url,
              "timeout": request_timeout, "max_retries": 0}

    proxy_url = ProxyConfigManager().get_proxy_url()
    hostname = urlsplit(base_url or "https://api.openai.com/v1").hostname or ""
    bypass_hosts = ",".join(os.getenv(key, "") for key in ("OPENAI_NO_PROXY", "NO_PROXY", "no_proxy"))
    if proxy_bypass_environment(hostname, {"no": bypass_hosts}):
        # Explicit proxy=... ignores NO_PROXY; also disable environment proxy discovery.
        kwargs["http_client"] = httpx.Client(trust_env=False, timeout=request_timeout)
        logger.info("模型客户端直连: %s", hostname)
    elif proxy_url:
        kwargs["http_client"] = httpx.Client(proxy=proxy_url, timeout=request_timeout)
        logger.info(f"OpenAI 客户端走代理: {proxy_url}")

    return OpenAI(**kwargs)
