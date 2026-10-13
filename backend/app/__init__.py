from fastapi import Depends, FastAPI
from app.services.auth import get_current_user, require_admin




def create_app(lifespan) -> FastAPI:
    from .routers import auth, note, provider, model, config, chat, files, history
    app = FastAPI(title="BiliNote",lifespan=lifespan)
    app.include_router(auth.router, prefix="/api")
    app.include_router(config.public_router, prefix="/api")
    app.include_router(note.router, prefix="/api", dependencies=[Depends(get_current_user)])
    app.include_router(provider.router, prefix="/api", dependencies=[Depends(require_admin)])
    app.include_router(model.router, prefix="/api", dependencies=[Depends(require_admin)])
    app.include_router(model.client_router, prefix="/api", dependencies=[Depends(get_current_user)])
    app.include_router(config.router, prefix="/api", dependencies=[Depends(require_admin)])
    app.include_router(chat.router, prefix="/api", dependencies=[Depends(get_current_user)])
    app.include_router(history.router, prefix='/api')
    app.include_router(files.router, prefix='/api')
    app.include_router(files.legacy_router)

    @app.middleware("http")
    async def private_api_responses(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app
