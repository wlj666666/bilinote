"""Conservative exclusions for known text models; unknown deployments remain usable."""
import re


def is_text_only_model(model_name: str | None) -> bool:
    return bool(re.fullmatch(r"qwen-(?:plus|turbo|max)(?:-latest|-\d{4}-\d{2}-\d{2})?",
                             (model_name or "").strip().lower()))
