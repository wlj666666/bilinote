"""Durable execution state, separate from customer-visible note history."""
import time
from sqlalchemy import Column, Integer, String, Text, Float, ForeignKey, UniqueConstraint
from app.db.engine import Base


class TaskJob(Base):
    __tablename__ = 'task_jobs'
    task_id = Column(String, ForeignKey('video_tasks.task_id'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    state = Column(String(20), nullable=False, default='QUEUED', index=True)
    phase = Column(String(40), nullable=False, default='PENDING')
    message = Column(Text, nullable=False, default='已提交，等待调度')
    payload = Column(Text, nullable=False)
    attempt_token = Column(String(40), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    recoveries = Column(Integer, nullable=False, default=0)
    worker_pid = Column(Integer, nullable=True)
    heartbeat_at = Column(Float, nullable=True)
    created_at = Column(Float, nullable=False, default=time.time)
    started_at = Column(Float, nullable=True)
    finished_at = Column(Float, nullable=True)
    updated_at = Column(Float, nullable=False, default=time.time)
    next_retry_at = Column(Float, nullable=True)
    error_type = Column(String(80), nullable=True)
    error_detail = Column(Text, nullable=True)
    stage_times = Column(Text, nullable=False, default='{}')


class TaskSubmission(Base):
    __tablename__ = 'task_submissions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    request_id = Column(String(80), nullable=False)
    task_id = Column(String, nullable=False)
    fingerprint = Column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint('user_id', 'request_id'),)


class TaskSchedulerState(Base):
    __tablename__ = 'task_scheduler_state'
    id = Column(Integer, primary_key=True)
    last_user_id = Column(Integer, nullable=True)
