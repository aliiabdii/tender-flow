from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tenderflow.schemas.problems import Problem
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
    "buyer_not_found": (404, "Buyer not found"),
    "tender_not_found": (404, "Tender not found"),
    "precondition_required": (428, "A current If-Match precondition is required"),
    "precondition_failed": (412, "Resource version does not match"),
    "idempotency_conflict": (
        409,
        "Idempotency key was already used for a different request",
    ),
    "invalid_cursor": (422, "Invalid page cursor"),
    "processing_run_not_found": (404, "Processing run not found"),
    "processing_conflict": (
        409,
        "Processing inputs or state do not allow this command",
    ),
}


def _response(code: str, retry_after: int | None = None) -> JSONResponse:
    status, title = _ERRORS[code]
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        Problem(
            type=f"urn:tenderflow:problem:{code}", title=title, status=status, code=code
        ).model_dump(),
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
