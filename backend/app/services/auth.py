"""Opaque, revocable sessions. Only token digests are stored in the database."""
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import deque

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models.users import AuthSession, User

PASSWORD_ITERATIONS = 600_000
SESSION_SECONDS = 12 * 60 * 60
REMEMBER_SESSION_SECONDS = 30 * 24 * 60 * 60
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("密码需要 12–128 个字符")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or not 1 <= len(password) <= 128:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def user_info(user: User) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role, "enabled": user.enabled}


def create_user(db: Session, username: str, password: str, role: str = "user") -> User:
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,64}", username):
        raise ValueError("账号需为 3–64 位字母、数字、下划线、点或短横线")
    if role not in {"admin", "user"}:
        raise ValueError("无效的账号角色")
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise ValueError("账号已存在") from None
    return user


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "请先登录", headers={"WWW-Authenticate": "Bearer"})
    session = db.get(AuthSession, token_digest(credentials.credentials))
    user = db.get(User, session.user_id) if session and session.expires_at > time.time() else None
    if user is None or not user.enabled:
        raise HTTPException(401, "登录已过期，请重新登录", headers={"WWW-Authenticate": "Bearer"})
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "仅管理员可以访问")
    return user


class LoginLimiter:
    """Bounded per-process throttling; applies to successes and failures alike."""

    def __init__(self, action: str = "登录"):
        self._attempts: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._action = action

    def check(self, peer: str):
        now = time.monotonic()
        with self._lock:
            # Expire idle peers so arbitrary usernames/IPs cannot grow this forever.
            self._attempts = {key: value for key, value in self._attempts.items() if value and value[-1] > now - 60}
            attempts = self._attempts.setdefault(peer, deque())
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if len(attempts) >= 10 or len(self._attempts) > 10_000:
                raise HTTPException(429, f"{self._action}尝试过于频繁，请稍后重试", headers={"Retry-After": "60"})
            attempts.append(now)


login_limiter = LoginLimiter()
registration_limiter = LoginLimiter("注册")
