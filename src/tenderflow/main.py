from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from tenderflow.api import admin, auth
from tenderflow.api.problems import register_problem_handlers
from tenderflow.config import Settings
from tenderflow.infrastructure.database.sessions import (
    create_database_engine,
    database_is_ready,
)


def create_app(*, settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings()

    engine = (
        create_database_engine(settings) if settings.database_url is not None else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            if engine is not None:
                engine.dispose()

    app = FastAPI(title="TenderFlow", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    register_problem_handlers(app)
    app.include_router(auth.router)
    app.include_router(admin.router)

    @app.middleware("http")
    async def prevent_authentication_caching(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/api/v1/auth", "/api/v1/admin")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def readiness() -> JSONResponse:
        ready = settings.local_signing_key is not None and database_is_ready(engine)
        return JSONResponse(
            {"status": "ready" if ready else "not_ready"},
            status_code=200 if ready else 503,
        )

    return app
