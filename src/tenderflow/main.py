from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

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

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def readiness() -> JSONResponse:
        ready = database_is_ready(engine)
        return JSONResponse(
            {"status": "ready" if ready else "not_ready"},
            status_code=200 if ready else 503,
        )

    return app
