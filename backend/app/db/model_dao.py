from app.db.engine import get_db
from app.db.models.models import Model
from app.db.models.providers import Provider


def _serialize(model: Model) -> dict:
    return {
        "id": model.id,
        "provider_id": model.provider_id,
        "model_name": model.model_name,
        "supports_image_input": model.supports_image_input,
        "created_at": model.created_at.isoformat() if model.created_at else None,
    }


def get_model_by_provider_and_name(provider_id: str, model_name: str):
    db = next(get_db())
    try:
        model = db.query(Model).filter_by(provider_id=provider_id, model_name=model_name).first()
        return _serialize(model) if model else None
    finally:
        db.close()


def insert_model(provider_id: str, model_name: str, supports_image_input: bool | None = None):
    db = next(get_db())
    try:
        model = Model(
            provider_id=provider_id,
            model_name=model_name,
            supports_image_input=supports_image_input,
        )
        db.add(model)
        db.commit()
        db.refresh(model)
        return _serialize(model)
    finally:
        db.close()


def upsert_models(provider_id: str, models: list[tuple[str, bool | None]]) -> list[dict]:
    """Add recommended models and refresh their capabilities without duplicate rows."""
    db = next(get_db())
    try:
        names = [name for name, _ in models]
        existing = {
            model.model_name: model
            for model in db.query(Model).filter(
                Model.provider_id == provider_id, Model.model_name.in_(names)
            ).all()
        }
        for name, supports_image_input in models:
            model = existing.get(name)
            if model is None:
                model = Model(
                    provider_id=provider_id,
                    model_name=name,
                    supports_image_input=supports_image_input,
                )
                db.add(model)
                existing[name] = model
            else:
                model.supports_image_input = supports_image_input
        db.commit()
        return [_serialize(existing[name]) for name in names]
    finally:
        db.close()


def get_models_by_provider(provider_id: str):
    db = next(get_db())
    try:
        return [_serialize(model) for model in db.query(Model).filter_by(provider_id=provider_id).all()]
    finally:
        db.close()


def delete_model(model_id: int):
    db = next(get_db())
    try:
        model = db.query(Model).filter_by(id=model_id).first()
        if model:
            db.delete(model)
            db.commit()
    finally:
        db.close()


def get_all_models():
    db = next(get_db())
    try:
        models = (
            db.query(Model)
            .join(Provider, Model.provider_id == Provider.id)
            .filter(Provider.enabled == 1)
            .all()
        )
        return [_serialize(model) for model in models]
    finally:
        db.close()
