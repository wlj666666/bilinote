"""Reserve the listening port before importing the app or recovering tasks."""
import errno
import json
import os
import socket
from urllib.request import ProxyHandler, build_opener


def _existing_bilinote(host: str, port: int) -> bool:
    # The probe is local and must not go through the user's configured proxy.
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    address = f"[{host}]" if ":" in host else host
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://{address}:{port}/openapi.json", timeout=2) as response:
            schema = json.loads(response.read(1024 * 1024))
        return (
            isinstance(schema, dict)
            and schema.get("info", {}).get("title") == "BiliNote"
            and "/api/generate_note" in schema.get("paths", {})
            and "/api/sys_check" in schema.get("paths", {})
        )
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def reserve_backend_socket(host: str, port: int) -> socket.socket | None:
    """Keep the port reserved through startup; None means BiliNote is running.

    A bind-and-close preflight would race with another simultaneous launch.
    Pass this same listening socket to Uvicorn instead.
    """
    listener = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    try:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(2048)
        listener.setblocking(False)
        return listener
    except OSError as exc:
        listener.close()
        if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
            if _existing_bilinote(host, port):
                print(
                    f"BiliNote 后端已在 {host}:{port} 运行，本次不重复启动。\n"
                    "如需加载代码修改，请先在原后端终端按 Ctrl+C 停止，再重新启动。",
                    flush=True,
                )
                return None
            raise SystemExit(
                f"无法启动：{host}:{port} 已被占用，可能有后端正在启动或其他程序使用此端口。\n"
                "请先确认并停止原进程，再重试；本次未执行任务恢复。"
            ) from None
        raise SystemExit(f"无法监听 {host}:{port}：{exc}") from None
