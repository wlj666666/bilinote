"""Run from backend/: python scripts/manage_users.py init-admin USERNAME."""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.engine import SessionLocal
from app.db.init_db import init_db
from app.db.models.users import AuthSession, User
from app.services.auth import create_user, hash_password


def main():
    parser = argparse.ArgumentParser(description="BiliNote 服务器账号管理（密码通过交互输入）")
    parser.add_argument("command", choices=["init-admin", "create-user", "reset-password"])
    parser.add_argument("username")
    args = parser.parse_args()
    init_db()
    with SessionLocal() as db:
        if args.command == "init-admin" and db.query(User).filter_by(role="admin").first():
            parser.error("管理员已存在；需要恢复密码时使用 reset-password")
        user = db.query(User).filter_by(username=args.username.strip().lower()).first()
        if args.command == "reset-password" and user is None:
            parser.error("账号不存在")
        password = getpass.getpass("密码（12–128 字符）：")
        if password != getpass.getpass("再次输入密码："):
            parser.error("两次密码不一致")
        try:
            if args.command == "reset-password":
                user.password_hash = hash_password(password)
                db.query(AuthSession).filter_by(user_id=user.id).delete()
                db.commit()
            else:
                create_user(db, args.username, password, "admin" if args.command == "init-admin" else "user")
        except ValueError as exc:
            parser.error(str(exc))
    print("账号操作完成")
    from app.services.legacy_migration import migrate_legacy_data
    migrate_legacy_data()


if __name__ == "__main__":
    main()
