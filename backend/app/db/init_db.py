from app.db.models.models import Model
from app.db.models.providers import Provider
from app.db.models.video_tasks import VideoTask
from app.db.models.users import User, AuthSession
from app.db.models.user_data import UserFile, ChatEntry, DataMigration, DeletedTask
from app.db.engine import get_engine, Base
from sqlalchemy import inspect, text

def init_db():
    engine = get_engine()

    # Back up existing SQLite data before adding ownership columns.
    inspector = inspect(engine)
    if 'video_tasks' in inspector.get_table_names() and 'user_id' not in {c['name'] for c in inspector.get_columns('video_tasks')}:
        from app.services.legacy_migration import backup_database
        backup_database(engine)

    Base.metadata.create_all(bind=engine)
    user_columns = {column['name'] for column in inspect(engine).get_columns('users')}
    with engine.begin() as connection:
        for name in ('created_at', 'last_login_at'):
            if name not in user_columns:
                connection.execute(text(f'ALTER TABLE users ADD COLUMN {name} INTEGER'))
    task_columns = {column['name'] for column in inspect(engine).get_columns('video_tasks')}
    additions = {'user_id': 'INTEGER', 'storage_relpath': "VARCHAR NOT NULL DEFAULT ''",
                 'form_data': 'TEXT', 'display_data': 'TEXT', 'deleted': 'BOOLEAN NOT NULL DEFAULT 0'}
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in task_columns:
                connection.execute(text(f'ALTER TABLE video_tasks ADD COLUMN {name} {sql_type}'))
        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_video_tasks_user_id ON video_tasks (user_id)'))
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
