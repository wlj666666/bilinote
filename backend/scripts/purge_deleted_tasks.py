"""Purge only previously deleted tasks; run with the backend stopped."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from app.db import engine as db_module
from app.db.init_db import init_db
from app.db.models.users import User
from app.db.models.video_tasks import VideoTask
from app.services.task_deletion import purge_task


def main():
    parser = argparse.ArgumentParser(description='清理指定账号已删除任务，保留未删除任务和共用文件')
    parser.add_argument('--user', required=True)
    parser.add_argument('--apply', action='store_true', help='执行清理；默认只显示数量')
    parser.add_argument('--compact', action='store_true', help='清理后压缩 SQLite 数据库，需要停止后端')
    args = parser.parse_args()
    with db_module.SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == args.user))
        if user is None:
            parser.error('账号不存在')
        user_id = user.id
        ids = list(db.scalars(select(VideoTask.task_id).where(VideoTask.user_id == user_id, VideoTask.deleted.is_(True))).all())
    if not args.apply:
        print(json.dumps({'marked_deleted': len(ids), 'applied': False}))
        return
    init_db()
    import os
    totals = {'tasks_removed': 0, 'files_removed': 0, 'bytes_removed': 0}
    for task_id in ids:
        result = purge_task(task_id, user_id, os.getenv('NOTE_OUTPUT_DIR', 'note_results'))
        for key in totals:
            totals[key] += result[key]
    if args.compact and db_module.engine.url.get_backend_name() == 'sqlite':
        with db_module.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.execute(text('VACUUM'))
    print(json.dumps({'applied': True, **totals}))


if __name__ == '__main__':
    main()
