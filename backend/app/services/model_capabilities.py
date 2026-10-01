"""Model image-input capabilities used by BiliNote's screenshot-based video understanding."""
import json
import re
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

# Curated from the Alibaba catalog and verified with this workspace's API key.
# The order is the recommended order for BiliNote's video-note workflow.
RECOMMENDED_QWEN_MODELS: list[tuple[str, bool]] = [
    ("qwen3.8-2.4t-a95b", False),
    ("deepseek-v4-pro", False),
    ("glm-5.3", False),
    ("qwen3.7-max", False),
    ("qwen3.6-max-preview", False),
    ("qwen3.8-max", True),
    ("qwen3.7-max-2026-06-08", True),
    ("kimi-k3", True),
    ("qwen3.7-plus", True),
    ("qwen3-vl-235b-a22b-thinking", True),
]
RECOMMENDED_CAPABILITIES = dict(RECOMMENDED_QWEN_MODELS)


def is_text_only_model(model_name: str | None) -> bool:
    """Compatibility fallback for older records that have no capability value."""
    return bool(re.fullmatch(
        r"qwen-(?:plus|turbo|max)(?:-latest|-\d{4}-\d{2}-\d{2})?",
        (model_name or "").strip().lower(),
    ))


def qwen_catalog_image_capability(provider: dict, model_name: str) -> bool | None:
    """Read the official model metadata for an arbitrary Qwen-provider model ID.

    None means the catalog was unavailable or did not declare text output.
    """
    if str(provider.get("id", "")).lower() != "qwen":
        return None
    parsed = urlparse(provider.get("base_url") or "")
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (
        host.endswith(".maas.aliyuncs.com") or host in {
            "dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com",
            "cn-hongkong.dashscope.aliyuncs.com",
        }
    ):
        return None
    key = provider.get("api_key")
    if not key:
        return None
    catalog_url = f"{parsed.scheme}://{parsed.netloc}/api/v1/models?" + urlencode({
        "model": model_name, "page_no": 1, "page_size": 20,
    })
    request = Request(catalog_url, headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json",
    })
    try:
        with urlopen(request, timeout=8) as response:
            payload = json.load(response)
        if not payload.get("success"):
            return None
        for entry in (payload.get("output") or {}).get("models") or []:
            if entry.get("model") != model_name:
                continue
            metadata = entry.get("inference_metadata") or {}
            if "Text" not in (metadata.get("response_modality") or []):
                return None
            return "Image" in (metadata.get("request_modality") or [])
    except Exception:
        return None
    return None


def saved_image_capability(provider_id: str, model_name: str) -> bool | None:
    """Use the saved capability; unknown models stay unknown."""
    from app.db.model_dao import get_model_by_provider_and_name

    record = get_model_by_provider_and_name(provider_id, model_name)
    if record and record["supports_image_input"] is not None:
        return record["supports_image_input"]
    if provider_id == "qwen":
        if model_name in RECOMMENDED_CAPABILITIES:
            return RECOMMENDED_CAPABILITIES[model_name]
        if is_text_only_model(model_name):
            return False
    return None
