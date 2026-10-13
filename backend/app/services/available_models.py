from fastapi import HTTPException

from app.db.model_dao import get_all_models, get_model_by_provider_and_name
from app.db.provider_dao import get_enabled_providers, get_provider_by_id


def available_models() -> list[dict]:
    # Only models explicitly saved under an enabled, configured provider are offered.
    providers = {provider.id for provider in get_enabled_providers() if provider.api_key}
    from app.services.model import ModelService

    models = ModelService._format_models(get_all_models())
    return [
        {key: model[key] for key in ("id", "provider_id", "model_name", "supports_image_input")}
        for model in models if model["provider_id"] in providers
    ]


def require_available_model(provider_id: str, model_name: str):
    provider = get_provider_by_id(provider_id)
    model = get_model_by_provider_and_name(provider_id, model_name)
    if not provider or provider.enabled != 1 or not provider.api_key or not model:
        raise HTTPException(403, "所选模型未开放使用，请重新选择")
