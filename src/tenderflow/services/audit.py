import re
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.orm import Session

from tenderflow.infrastructure.database.models.audit import AuditEvent
from tenderflow.schemas.identity import ROLES, Actor


def write_audit(
    *,
    session: Session,
    actor: Actor | None,
    action: str,
    entity_id: UUID,
    metadata: dict[str, JsonValue],
) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", action):
        raise ValueError("Invalid audit action code")
    for key, value in metadata.items():
        if key == "roles":
            valid = isinstance(value, list) and all(v in ROLES for v in value)
        elif key in {
            "credential_version",
            "input_version",
            "selection_version",
            "preparation_version",
            "mapping_version",
        }:
            valid = type(value) is int and value >= 0
        elif key in {
            "session_family_id",
            "user_id",
            "run_id",
            "document_id",
            "line_id",
        }:
            try:
                UUID(value)
                valid = True
            except (ValueError, TypeError, AttributeError):
                valid = False
        elif key == "status":
            valid = value in {"ACTIVE", "INACTIVE"}
        elif key == "reason_code":
            valid = value in {
                "BOOTSTRAP",
                "RESET",
                "LOGOUT",
                "REPLAY",
                "ACTIVATION",
                "UPDATE",
            }
        else:
            valid = False
        if not valid:
            raise ValueError("Unsafe audit metadata")
    session.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            action=action,
            entity_id=entity_id,
            details=metadata,
        )
    )
