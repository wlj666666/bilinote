import json
from io import BytesIO
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import engine as engine_module
from app.db import init_db as init_db_module
from app.db.model_dao import get_all_models
from app.db.models.providers import Provider
from app.services.model import ModelService
from app.services.model_capabilities import (
    RECOMMENDED_QWEN_MODELS,
    qwen_catalog_image_capability,
    saved_image_capability,
)
from app.services.provider import ProviderService


class FakeResponse(BytesIO):
    pass


def test_recommended_models_are_five_text_and_five_image():
    assert len(RECOMMENDED_QWEN_MODELS) == 10
    assert len({name for name, _ in RECOMMENDED_QWEN_MODELS}) == 10
    assert sum(image for _, image in RECOMMENDED_QWEN_MODELS) == 5


def test_legacy_db_migrates_and_import_is_idempotent():
    temporary_engine = create_engine("sqlite:///:memory:")
    with temporary_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE models (id INTEGER PRIMARY KEY, provider_id VARCHAR, "
            "model_name VARCHAR, created_at DATETIME)"
        ))
        connection.execute(text(
            "INSERT INTO models (provider_id, model_name) VALUES ('qwen', 'qwen-plus')"
        ))
    with patch.object(init_db_module, "get_engine", return_value=temporary_engine), patch.object(
        engine_module, "SessionLocal", sessionmaker(bind=temporary_engine)
    ):
        init_db_module.init_db()
        init_db_module.init_db()
        session = engine_module.SessionLocal()
        session.add(Provider(
            id="qwen", name="Qwen", logo="Qwen", type="built-in",
            api_key="test-key", base_url="https://example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            enabled=1,
        ))
        session.commit()
        session.close()
        with patch.object(ProviderService, "get_provider_by_id", return_value={
            "id": "qwen", "api_key": "test-key",
        }):
            assert len(ModelService.import_recommended_models("qwen")) == 10
            assert len(ModelService.import_recommended_models("qwen")) == 10
        models = get_all_models()
        ordered = ModelService.get_all_models()
        assert [item["model_name"] for item in ordered] == [
            "qwen-plus", *(name for name, _ in RECOMMENDED_QWEN_MODELS)
        ]
        assert len(models) == 11
        assert next(m for m in models if m["model_name"] == "qwen-plus")["supports_image_input"] is False
        assert sum(m["supports_image_input"] is True for m in models) == 5
        assert saved_image_capability("qwen", "qwen3.8-max") is True
        assert saved_image_capability("qwen", "deepseek-v4-pro") is False


def test_qwen_catalog_reads_modalities_and_rejects_image_generators():
    provider = {
        "id": "qwen", "api_key": "test-key",
        "base_url": "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    }

    def response_for(request_modalities, response_modalities):
        data = {"success": True, "output": {"models": [{
            "model": "candidate", "inference_metadata": {
                "request_modality": request_modalities,
                "response_modality": response_modalities,
            },
        }]}}
        return FakeResponse(json.dumps(data).encode())

    with patch("app.services.model_capabilities.urlopen", return_value=response_for(["Text", "Image"], ["Text"])):
        assert qwen_catalog_image_capability(provider, "candidate") is True
    with patch("app.services.model_capabilities.urlopen", return_value=response_for(["Text"], ["Text"])):
        assert qwen_catalog_image_capability(provider, "candidate") is False
    with patch("app.services.model_capabilities.urlopen", return_value=response_for(["Text"], ["Image"])):
        assert qwen_catalog_image_capability(provider, "candidate") is None
    assert qwen_catalog_image_capability({**provider, "base_url": "https://example.com/v1"}, "candidate") is None
