from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint

from app.db.engine import Base


class UserFile(Base):
    __tablename__ = 'user_files'
    __table_args__ = (UniqueConstraint('kind', 'path', name='uq_user_file_path'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    task_id = Column(String, nullable=True, index=True)
    kind = Column(String(16), nullable=False)
    path = Column(String, nullable=False)


class ChatEntry(Base):
    __tablename__ = 'chat_entries'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    task_id = Column(String, nullable=False, index=True)
    payload = Column(Text, nullable=False)


class DataMigration(Base):
    __tablename__ = 'data_migrations'

    name = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False)


class DeletedTask(Base):
    """Minimal tombstone; never retain a deleted note or its generation options."""
    __tablename__ = 'deleted_tasks'

    task_id = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
