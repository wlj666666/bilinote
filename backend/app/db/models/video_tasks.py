from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, DateTime, Text, func
from sqlalchemy.orm import declarative_base

from app.db.engine import Base


class VideoTask(Base):
    __tablename__ = "video_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String, nullable=False)
    platform = Column(String, nullable=False)
    task_id = Column(String, unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)
    storage_relpath = Column(String, nullable=False, default='')
    form_data = Column(Text, nullable=True)
    display_data = Column(Text, nullable=True)
    deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())
