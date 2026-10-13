"""Run from the backend working directory, using the same DATABASE_URL."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.init_db import init_db
from app.services.legacy_migration import migrate_legacy_data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='备份数据库并将旧数据归到管理员')
    parser.add_argument('--admin', help='原管理员账号，默认最早创建的管理员')
    args = parser.parse_args()
    init_db()
    print(json.dumps(migrate_legacy_data(admin_username=args.admin), ensure_ascii=False, indent=2))
