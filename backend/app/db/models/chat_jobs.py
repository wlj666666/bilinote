"""Persist short HTTP chat submissions across polling connections."""
import time

from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text, UniqueConstraint

from app.db.engine import Base


class ChatJob(Base):
    __tablename__ = 'chat_jobs'
    __table_args__ = (UniqueConstraint('user_id', 'request_id', name='uq_chat_request'),)

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    task_id = Column(String, nullable=False, index=True)
    request_id = Column(String(80), nullable=False)
    state = Column(String(16), nullable=False, default='queued', index=True)
    payload = Column(Text, nullable=False)
    result = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(Float, nullable=False, default=time.time)
    finished_at = Column(Float, nullable=True)
