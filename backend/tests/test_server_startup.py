"""Real socket/process regressions for duplicate backend launches."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

import pytest

from server_startup import reserve_backend_socket


@contextmanager
def http_service(schema):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(schema).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_port_stays_reserved_and_is_reusable_after_shutdown(monkeypatch):
    monkeypatch.setattr("server_startup._existing_bilinote", lambda *args: False)
    with reserve_backend_socket("127.0.0.1", 0) as listener:
        port = listener.getsockname()[1]
        with pytest.raises(SystemExit, match="已被占用"):
            reserve_backend_socket("127.0.0.1", port)
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    with reserve_backend_socket("127.0.0.1", port) as restarted:
        assert restarted.getsockname()[1] == port


@pytest.mark.parametrize("schema", [
    {"info": {"title": "another app"}},
    {"info": {"title": "BiliNote"}, "paths": {}},
    {"info": None},
    [],
])
def test_foreign_service_is_not_treated_as_running_backend(schema):
    with http_service(schema) as port:
        with pytest.raises(SystemExit, match="已被占用"):
            reserve_backend_socket("127.0.0.1", port)
        # The original service must remain alive.
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass


def test_duplicate_main_exits_before_imports_or_task_recovery(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    state = tmp_path / "running.status.json"
    state.write_text('{"status":"SUMMARIZING"}')
    original = state.read_bytes()
    schema = {"info": {"title": "BiliNote"}, "paths": {
        "/api/generate_note": {}, "/api/sys_check": {},
    }}
    with http_service(schema) as port:
        env = {**os.environ, "BACKEND_HOST": "127.0.0.1", "BACKEND_PORT": str(port),
               "NOTE_OUTPUT_DIR": str(tmp_path), "HTTP_PROXY": "http://127.0.0.1:1"}
        result = subprocess.run([sys.executable, str(backend / "main.py")],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert "本次不重复启动" in result.stdout
        assert "[startup" not in result.stdout + result.stderr
        assert not (tmp_path / "logs").exists()
        assert state.read_bytes() == original


def test_permission_error_is_not_reported_as_duplicate(monkeypatch):
    import errno
    from unittest.mock import Mock

    listener = Mock()
    listener.bind.side_effect = PermissionError(errno.EACCES, "Permission denied")
    monkeypatch.setattr("server_startup.socket.socket", lambda *args: listener)
    with pytest.raises(SystemExit, match="无法监听"):
        reserve_backend_socket("127.0.0.1", 8483)
    listener.close.assert_called_once()
