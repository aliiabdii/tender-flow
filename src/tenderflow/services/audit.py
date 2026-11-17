import re
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.orm import Session

from tenderflow.infrastructure.database.models.audit import AuditEvent
from tenderflow.schemas.identity import ROLES, Actor


def _safe_value(key: str, value: JsonValue) -> bool:
    if key == "roles":
        return isinstance(value, list) and all(
            isinstance(v, str) and v in ROLES for v in value
        )
    if key in {
        "credential_version",
        "input_version",
        "selection_version",
        "preparation_version",
        "mapping_version",
        "content_revision",
        "source_revision",
    }:
        return type(value) is int and value >= 0
    if key in {
        "session_family_id",
        "user_id",
        "run_id",
        "document_id",
        "line_id",
        "tenant_id",
        "object_id",
        "external_identity_id",
        "buyer_id",
        "tender_id",
        "supersedes_tender_id",
    }:
        try:
            UUID(value)
            return True
        except (ValueError, TypeError, AttributeError):
            return False
    if key == "status":
        return isinstance(value, str) and value in {"ACTIVE", "INACTIVE"}
    if key == "reason_code":
        return isinstance(value, str) and value in {
            "BOOTSTRAP",
            "RESET",
            "LOGOUT",
            "REPLAY",
            "ACTIVATION",
            "UPDATE",
        }
    return False


def sanitized_metadata(metadata: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {key: value for key, value in metadata.items() if _safe_value(key, value)}


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
    if len(sanitized_metadata(metadata)) != len(metadata):
        raise ValueError("Unsafe audit metadata")
    session.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            action=action,
            entity_id=entity_id,
            details=metadata,
        )
    )
