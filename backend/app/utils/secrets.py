"""Redact configured credentials from logs, including formatted tracebacks."""
import os
import re
import threading

_values: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str | None, cookie: bool = False):
    if not value:
        return
    with _lock:
        _values.add(value)
        if cookie:
            for item in value.split(";"):
                _, separator, token = item.strip().partition("=")
                if separator and len(token) >= 6:
                    _values.add(token)


def redact_secrets(text: str) -> str:
    with _lock:
        values = sorted(_values, key=len, reverse=True)
    for value in values:
        text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9_.~+/-]+", r"\1[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(https?://)[^\s/@:]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    return text


def customer_result(value):
    """Downloader metadata may contain credential-bearing headers or cookie jars."""
    if isinstance(value, dict):
        return {
            key: customer_result(item) for key, item in value.items()
            if key.lower() not in {"api_key", "apikey", "cookie", "cookies", "cookiefile", "http_headers", "headers", "authorization", "password", "base_url"}
        }
    if isinstance(value, list):
        return [customer_result(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


for _name, _value in os.environ.items():
    if "API_KEY" in _name or _name.endswith("_TOKEN"):
        register_secret(_value)
