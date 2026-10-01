from sqlalchemy import Boolean, Column, DateTime, Integer, String, func

from app.db.engine import Base


class Model(Base):
    __tablename__ = "models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String, nullable=False)
    model_name = Column(String, nullable=False)
    # NULL means the provider has not supplied reliable capability metadata.
    supports_image_input = Column(Boolean, nullable=True)
    created_at = Column(DateTime, server_default=func.now())