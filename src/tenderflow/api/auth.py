from fastapi import APIRouter, Depends, Request, Response

from tenderflow.api.dependencies import current_actor, get_identity_runtime
from tenderflow.schemas.identity import (
    ActivationRequest,
    Actor,
    LoginRequest,
    RefreshRequest,
    TokenPair,
    UserView,
)
from tenderflow.services.identity import (
    IdentityRuntime,
    activate_local,
    authenticate_local,
    current_user,
    logout_local,
    refresh_local,
)

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.post("/local/login", response_model=TokenPair)
def login(
    command: LoginRequest,
    request: Request,
    runtime: IdentityRuntime = Depends(get_identity_runtime),
) -> TokenPair:
    return authenticate_local(
        login=command.login,
        password=command.password.get_secret_value(),
        client_ip=request.client.host if request.client else None,
        runtime=runtime,
    )


@router.post("/local/refresh", response_model=TokenPair)
def refresh(
    command: RefreshRequest, runtime: IdentityRuntime = Depends(get_identity_runtime)
) -> TokenPair:
    return refresh_local(
        refresh_token=command.refresh_token.get_secret_value(), runtime=runtime
    )


@router.post("/local/logout", status_code=204)
def logout(
    command: RefreshRequest, runtime: IdentityRuntime = Depends(get_identity_runtime)
) -> Response:
    logout_local(
        refresh_token=command.refresh_token.get_secret_value(), runtime=runtime
    )
    return Response(status_code=204)


@router.post("/local/activate", status_code=204)
def activate(
    command: ActivationRequest, runtime: IdentityRuntime = Depends(get_identity_runtime)
) -> Response:
    activate_local(
        token=command.token.get_secret_value(),
        password=command.password.get_secret_value(),
        runtime=runtime,
    )
    return Response(status_code=204)


@router.get("/me", response_model=UserView)
def me(
    actor: Actor = Depends(current_actor),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
) -> UserView:
    return current_user(actor=actor, runtime=runtime)
