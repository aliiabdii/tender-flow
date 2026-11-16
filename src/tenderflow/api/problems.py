from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tenderflow.services.errors import ServiceError

_ERRORS = {
    "invalid_credentials": (401, "Invalid authentication credentials"),
    "forbidden": (403, "Permission denied"),
    "user_not_found": (404, "User not found"),
    "login_taken": (409, "Login name is already assigned"),
    "administrator_exists": (409, "An administrator has already been provisioned"),
    "invalid_password": (422, "Password does not meet the configured length policy"),
    "invalid_request": (422, "Request validation failed"),
    "rate_limited": (429, "Too many authentication attempts"),
    "authentication_unavailable": (503, "Authentication is not configured"),
    "identity_provider_unavailable": (
        503,
        "Identity provider is temporarily unavailable",
    ),
    "invalid_identity_link": (
        422,
        "Identity must belong to the configured Entra tenant",
    ),
    "identity_already_linked": (409, "External identity is already linked"),
    "identity_not_found": (404, "External identity not found"),
}


def _response(code: str, retry_after: int | None = None) -> JSONResponse:
    status, title = _ERRORS[code]
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        {
            "type": f"urn:tenderflow:problem:{code}",
            "title": title,
            "status": status,
            "code": code,
        },
        status_code=status,
        media_type="application/problem+json",
        headers=headers,
    )


def register_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError):
        return _response(error.code, error.retry_after)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return _response("invalid_request")
