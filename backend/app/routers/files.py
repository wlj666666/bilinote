from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.db.models.users import User
from app.services.auth import get_current_user
from app.services.storage_scope import static_root, uploads_root
from app.services.user_data import require_file

router = APIRouter()
legacy_router = APIRouter()


def private_file(kind: str, path: str, user: User):
    roots = {'static': static_root(), 'uploads': uploads_root()}
    if kind not in roots:
        raise HTTPException(404, '文件不存在')
    # Route parameters are already decoded by Starlette; do not decode twice.
    actual = require_file(kind, path, user.id, roots[kind])
    return FileResponse(actual, headers={'Cache-Control': 'no-store', 'Vary': 'Authorization'})


@router.get('/files/{kind}/{path:path}')
def download_file(kind: str, path: str, user: User = Depends(get_current_user)):
    return private_file(kind, path, user)


@legacy_router.get('/static/{path:path}')
def legacy_static(path: str, user: User = Depends(get_current_user)):
    return private_file('static', path, user)


@legacy_router.get('/uploads/{path:path}')
def legacy_upload(path: str, user: User = Depends(get_current_user)):
    return private_file('uploads', path, user)


def local_file_reference(url: str) -> tuple[str, str] | None:
    path = urlparse(url).path
    for prefix, kind in [('/static/', 'static'), ('/uploads/', 'uploads'), ('/api/files/static/', 'static'), ('/api/files/uploads/', 'uploads')]:
        if path.startswith(prefix):
            return kind, unquote(path[len(prefix):])
    return None
