from fastapi import FastAPI

from tenderflow.config import Settings


def create_app(*, settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings()

    app = FastAPI(title="TenderFlow")
    app.state.settings = settings

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    return app
