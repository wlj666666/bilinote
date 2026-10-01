from app.db.models.models import Model
from app.db.models.providers import Provider
from app.db.models.video_tasks import VideoTask
from app.db.engine import get_engine, Base
from sqlalchemy import inspect, text

def init_db():
    engine = get_engine()

    Base.metadata.create_all(bind=engine)
    # create_all does not add columns to an existing installation.
    columns = {column["name"] for column in inspect(engine).get_columns("models")}
    if "supports_image_input" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE models ADD COLUMN supports_image_input BOOLEAN"))
    # Existing installations already have qwen-plus saved as a text-only model.
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE models SET supports_image_input = 0 "
            "WHERE provider_id = 'qwen' AND model_name = 'qwen-plus' "
            "AND supports_image_input IS NULL"
        ))
