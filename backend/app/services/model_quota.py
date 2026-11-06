"""Read Alibaba free-token balances using the official Bailian CLI console session.

API contract: modelstudioai/cli, commands/usage/shared.ts and console/gateway.ts.
Inference API keys and per-minute rate limits are not free-token balances.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

REFRESH_SECONDS = 60
_API = "zeldaEasy.bailian-commerce.freeTrial.queryFreeTierQuota"
_lock = threading.Lock()
_cache: dict = {}
_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "bailian" / "config.json"


def _read_session() -> dict:
    directory = os.getenv("BAILIAN_CONFIG_DIR")
    path = Path(directory) / "config.json" if directory else _DEFAULT_CONFIG
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            return {}
        active = config.get("active_config", "default")
        if active != "default":
            config = config.get(active) or {}
        return config if isinstance(config, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _extract_quotas(payload: dict) -> list:
    data = payload.get("data") or {}
    if "DataV2" in data:
        data = ((data.get("DataV2") or {}).get("data") or {}).get("data") or {}
    else:
        data = data.get("data") or {}
    quotas = data.get("freeTierQuotas")
    if not isinstance(quotas, list):
        raise ValueError("Missing freeTierQuotas")
    return quotas


def _number(value):
    # Unknown, missing and malformed balances must never become a fake zero.
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return int(value)
    return None


def _normalize(row: dict, now: float) -> dict:
    remaining = _number(row.get("quotaTotal"))
    total = _number(row.get("quotaInitTotal"))
    expiry = _number(row.get("quotaValidityPeriod"))
    expires_at = expiry / 1000 if expiry else None  # Official API uses milliseconds.
    raw_status = row.get("quotaStatus")
    status = "unknown"
    if expires_at is not None and expires_at <= now:
        status = "expired"
    elif remaining == 0 and total is not None and total > 0:
        status = "exhausted"
    elif raw_status == "VALID" and remaining is not None and total is not None and total > 0:
        status = "available"
    elif raw_status == "UNKNOWN":
        status = "unsupported"
    return {
        "provider_id": "qwen", "model_name": row.get("model"),
        "remaining_tokens": remaining, "total_tokens": total,
        "expires_at": expires_at, "status": status,
    }


def quota_snapshot(provider: dict, model_names: list[str]) -> dict:
    names = tuple(sorted(set(model_names)))
    empty = [{"provider_id": provider.get("id"), "model_name": name,
              "remaining_tokens": None, "total_tokens": None,
              "expires_at": None, "status": "unknown"} for name in names]
    result = {"status": "unsupported", "message": "该供应商暂不支持额度查询",
              "updated_at": None, "refresh_interval_seconds": REFRESH_SECONDS, "models": empty}
    host = urlsplit(provider.get("base_url") or "").hostname or ""
    if provider.get("id") != "qwen" or not (
        host.endswith(".maas.aliyuncs.com") or host == "dashscope.aliyuncs.com"
    ):
        return result
    session = _read_session()
    token = session.get("access_token") or session.get("accessToken")
    if not token:
        return {**result, "status": "needs_login", "message": "请先完成阿里云控制台登录，才能查询真实剩余 Token 和到期时间"}
    # Do not mix a different CLI account/key or region with this provider.
    if (session.get("api_key") and session["api_key"] != provider.get("api_key")) or (
        session.get("console_region", "cn-beijing") != "cn-beijing"
        or session.get("console_site", "domestic") != "domestic"
    ):
        return {**result, "status": "needs_login", "message": "额度登录与当前模型供应商不匹配，请使用同一账号的北京地域登录"}
    if not names:
        return {**result, "status": "ok", "message": "暂无已添加模型", "models": []}
    cache_key = (hashlib.sha256(token.encode()).hexdigest(), session.get("console_switch_agent"), names)
    with _lock:
        now = time.time()
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < REFRESH_SECONDS:
            return cached[1]
        params = {"Api": _API, "V": "1.0", "Data": {
            "queryFreeTierQuotaRequest": {"models": list(names)},
            "cornerstoneParam": {"protocol": "V2", "console": "ONE_CONSOLE",
                "productCode": "p_efm", "switchUserType": 3, "consoleSite": "BAILIAN_ALIYUN"},
        }}
        if session.get("console_switch_agent") is not None:
            params["Data"]["cornerstoneParam"]["switchAgent"] = session["console_switch_agent"]
        url = "https://bailian-cs.console.aliyun.com/cli/api.json?" + urlencode({
            "action": "BroadScopeAspnGateway", "product": "sfm_bailian", "api": _API,
        })
        request = Request(url, data=urlencode({"params": json.dumps(params), "region": "cn-beijing"}).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=12) as response:
                payload = json.load(response)
            error = (payload.get("data") or {}).get("errorCode")
            if error:
                if "NotLogined" in str(error):
                    return {**result, "status": "needs_login", "message": "阿里云登录已过期，请重新登录后刷新额度"}
                raise ValueError("Quota gateway rejected request")
            normalized = {row.get("model"): _normalize(row, now) for row in _extract_quotas(payload)}
            result = {**result, "status": "ok", "message": "阿里云免费额度约每分钟更新；输入和输出 Token 共用余额",
                      "updated_at": time.time(), "models": [normalized.get(row["model_name"], row) for row in empty]}
            # Bound memory when configurations or saved model sets change.
            if len(_cache) >= 32:
                _cache.clear()
            _cache[cache_key] = (now, result)
            return result
        except Exception:
            # Never expose upstream messages, console credentials or old balances as fresh.
            return {**result, "status": "error", "message": "额度同步失败，请稍后刷新或重新完成阿里云登录"}


def saved_model_quotas() -> dict:
    from app.db.model_dao import get_models_by_provider
    from app.services.provider import ProviderService

    provider = ProviderService.get_provider_by_id("qwen")
    if not provider or not provider.get("api_key") or not provider.get("enabled"):
        return {"status": "unsupported", "message": "请先启用并配置 Qwen 供应商",
                "updated_at": None, "refresh_interval_seconds": REFRESH_SECONDS, "models": []}
    return quota_snapshot(provider, [row["model_name"] for row in get_models_by_provider("qwen")])
