import time
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models.users import AuthSession, User
from app.db.models.video_tasks import VideoTask
from app.services.auth import (
    DUMMY_PASSWORD_HASH, SESSION_SECONDS, REMEMBER_SESSION_SECONDS, create_user, get_current_user,
    login_limiter, registration_limiter, require_admin, token_digest, user_info, verify_password,
)
from app.utils.response import ResponseWrapper as R

router = APIRouter(prefix="/auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    remember_me: bool = False


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=128)


class RegisterRequest(CreateUserRequest):
    remember_me: bool = False


class UpdateUserRequest(BaseModel):
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=128)


@router.post("/login")
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    login_limiter.check(request.client.host if request.client else "unknown")
    user = db.query(User).filter_by(username=data.username.strip().lower()).first()
    valid = verify_password(data.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if not valid or user is None or not user.enabled:
        raise HTTPException(401, "账号或密码错误")
    return start_session(db, user, data.remember_me)


def start_session(db: Session, user: User, remember_me: bool):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    expiry = now + (REMEMBER_SESSION_SECONDS if remember_me else SESSION_SECONDS)
    user.last_login_at = now
    db.query(AuthSession).filter(AuthSession.expires_at <= now).delete()
    db.add(AuthSession(token_hash=token_digest(token), user_id=user.id, expires_at=expiry))
    db.commit()
    response = R.success({"token": token, "expires_at": expiry, "user": user_info(user)})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/register")
def register(data: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    registration_limiter.check(request.client.host if request.client else "unknown")
    try:
        user = create_user(db, data.username, data.password, role="user")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return start_session(db, user, data.remember_me)


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return R.success(user_info(user))


@router.post("/logout")
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    token = request.headers["Authorization"].split(" ", 1)[1]
    db.query(AuthSession).filter_by(token_hash=token_digest(token)).delete()
    db.commit()
    return R.success()


@router.get("/users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    # Count tasks in SQL; do not read users' private notes or chat contents.
    counts = dict(db.query(VideoTask.user_id, func.count(VideoTask.id))
                  .filter(VideoTask.deleted.is_(False)).group_by(VideoTask.user_id).all())
    return R.success([{**user_info(user), "created_at": user.created_at,
                       "last_login_at": user.last_login_at, "task_count": counts.get(user.id, 0)}
                      for user in db.query(User).order_by(User.id).all()])


@router.post("/users")
def add_user(data: CreateUserRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        # This endpoint never accepts a client-supplied role.
        user = create_user(db, data.username, data.password, role="user")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return R.success(user_info(user), msg="客户账号已创建")


@router.patch("/users/{user_id}")
def update_user(user_id: int, data: UpdateUserRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from app.services.auth import hash_password

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "账号不存在")
    if user.role == "admin":
        raise HTTPException(400, "管理员账号请通过服务器管理命令维护")
    if data.enabled is not None:
        user.enabled = data.enabled
    if data.password is not None:
        user.password_hash = hash_password(data.password)
    if data.password is not None or data.enabled is False:
        db.query(AuthSession).filter_by(user_id=user.id).delete()
    db.commit()
    return R.success(user_info(user))
