from uuid import UUID

from fastapi import APIRouter, Depends, Response

from tenderflow.api.dependencies import get_identity_runtime, require_roles
from tenderflow.schemas.identity import (
    ActivationSecret,
    Actor,
    EntraIdentityView,
    LinkEntraIdentity,
    ProvisionUser,
    UpdateUser,
    UserProvisioned,
    UserView,
)
from tenderflow.services.identity import (
    IdentityRuntime,
    link_entra_identity,
    provision_user,
    reset_local_password,
    unlink_entra_identity,
    update_user,
)

router = APIRouter(prefix="/api/v1/admin/users", tags=["user administration"])
_administrator = require_roles("admin")


@router.post("", response_model=UserProvisioned, status_code=201)
def provision(
    command: ProvisionUser,
    actor: Actor = Depends(_administrator),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
) -> UserProvisioned:
    return provision_user(actor=actor, command=command, runtime=runtime)


@router.patch("/{user_id}", response_model=UserView)
def change_user(
    user_id: UUID,
    command: UpdateUser,
    actor: Actor = Depends(_administrator),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
) -> UserView:
    return update_user(actor=actor, user_id=user_id, command=command, runtime=runtime)


@router.post(
    "/{user_id}/password-resets", response_model=ActivationSecret, status_code=201
)
def reset_password(
    user_id: UUID,
    actor: Actor = Depends(_administrator),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
) -> ActivationSecret:
    return reset_local_password(actor=actor, user_id=user_id, runtime=runtime)


@router.post(
    "/{user_id}/entra-identities", response_model=EntraIdentityView, status_code=201
)
def link_entra(
    user_id: UUID,
    command: LinkEntraIdentity,
    actor: Actor = Depends(_administrator),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
) -> EntraIdentityView:
    return link_entra_identity(
        actor=actor,
        user_id=user_id,
        tenant_id=str(command.tenant_id),
        object_id=str(command.object_id),
        runtime=runtime,
    )


@router.delete("/{user_id}/entra-identities/{identity_id}", status_code=204)
def unlink_entra(
    user_id: UUID,
    identity_id: UUID,
    actor: Actor = Depends(_administrator),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
) -> Response:
    unlink_entra_identity(
        actor=actor, user_id=user_id, identity_id=identity_id, runtime=runtime
    )
    return Response(status_code=204)
