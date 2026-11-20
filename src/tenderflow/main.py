from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from tenderflow.api import admin, auth, buyers, documents, processing, tenders
from tenderflow.api.problems import register_problem_handlers
from tenderflow.api.upload_limits import UploadLimits
from tenderflow.config import Settings
from tenderflow.infrastructure.auth.entra import EntraTokenValidator
from tenderflow.infrastructure.database.sessions import (
    create_database_engine,
    database_is_ready,
)
from tenderflow.infrastructure.storage import create_object_store


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

    app = FastAPI(
        title="TenderFlow",
        lifespan=lifespan,
        swagger_ui_init_oauth={"usePkceWithAuthorizationCodeGrant": True},
    )
    app.state.settings = settings
    app.state.object_store = create_object_store(settings)
    app.state.engine = engine
    app.state.entra_validator = (
        EntraTokenValidator(settings) if settings.entra_enabled else None
    )
    register_problem_handlers(app)
    app.add_middleware(UploadLimits, maximum=settings.max_upload_bytes)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(buyers.router)
    app.include_router(tenders.router)
    app.include_router(processing.router)
    app.include_router(documents.router)

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
        ready = (
            settings.local_signing_key is not None or settings.entra_enabled
        ) and database_is_ready(engine)
        return JSONResponse(
            {"status": "ready" if ready else "not_ready"},
            status_code=200 if ready else 503,
        )

    if settings.entra_enabled:

        def openapi():
            if app.openapi_schema is None:
                schema = get_openapi(
                    title=app.title, version=app.version, routes=app.routes
                )
                authority = f"https://login.microsoftonline.com/{settings.entra_tenant_id}/oauth2/v2.0"
                schema["components"]["securitySchemes"]["EntraOAuth2"] = {
                    "type": "oauth2",
                    "description": (
                        "Obtain a delegated TenderFlow API access token using "
                        "authorization code with PKCE in your registered public client. "
                        "Use a separate client registration from the API. "
                        "No client secret is needed or accepted by TenderFlow. "
                        "Only explicitly linked, active internal users are admitted."
                    ),
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": f"{authority}/authorize",
                            "tokenUrl": f"{authority}/token",
                            "scopes": {
                                settings.entra_required_scope: "Access TenderFlow as the signed-in user"
                            },
                        }
                    },
                }
                for path in schema["paths"].values():
                    for operation in path.values():
                        if isinstance(operation, dict) and {
                            "HTTPBearer": []
                        } in operation.get("security", []):
                            operation["security"].append(
                                {"EntraOAuth2": [settings.entra_required_scope]}
                            )
                app.openapi_schema = schema
            return app.openapi_schema

        app.openapi = openapi
    return app
