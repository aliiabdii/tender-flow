from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, JsonValue
from sqlalchemy.orm import Session

from tenderflow.api.dependencies import current_actor, database_session, require_roles
from tenderflow.api.pagination import (
    PageRequest,
    decode_cursor,
    make_page,
    page_request,
)
from tenderflow.api.preconditions import etag, expected_version
from tenderflow.schemas.identity import Actor
from tenderflow.schemas.tenders import Page, TenderCreate, TenderUpdate, TenderView
from tenderflow.services import tenders
from tenderflow.services.audit import sanitized_metadata

router = APIRouter(prefix="/api/v1/tenders", tags=["tenders"])
_manager = require_roles("tender_manager")


class AuditView(BaseModel):
    id: UUID
    actor_id: UUID | None
    action: str
    entity_id: UUID
    metadata: dict[str, JsonValue]
    created_at: datetime


def _view(tender, response):
    result = tenders.tender_view(tender)
    response.headers["ETag"] = etag(
        resource="tender",
        resource_id=tender.id,
        version=tender.content_revision,
        variant="late"
        if "submission_deadline_passed" in result.warnings
        else "current",
    )
    return result


@router.post("", response_model=TenderView, status_code=201)
def create(
    command: TenderCreate,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, max_length=128),
    actor: Actor = Depends(_manager),
    session: Session = Depends(database_session, scope="function"),
):
    tender = tenders.register_tender(
        session=session,
        actor=actor,
        command=command,
        key=idempotency_key,
        lifetime_seconds=request.app.state.settings.idempotency_seconds,
    )
    response.headers["Location"] = f"/api/v1/tenders/{tender.id}"
    return _view(tender, response)


@router.get("", response_model=Page[TenderView])
def listing(
    page: PageRequest = Depends(page_request),
    actor: Actor = Depends(current_actor),
    session: Session = Depends(database_session, scope="function"),
):
    rows = tenders.list_tenders(
        session=session,
        actor=actor,
        after=decode_cursor(page.cursor, scope="tenders"),
        limit=page.limit,
    )
    return make_page(
        rows, limit=page.limit, scope="tenders", project=tenders.tender_view
    )


@router.get("/{tender_id}", response_model=TenderView)
def get(
    tender_id: UUID,
    response: Response,
    actor: Actor = Depends(current_actor),
    session: Session = Depends(database_session, scope="function"),
):
    return _view(
        tenders.get_tender(session=session, actor=actor, tender_id=tender_id), response
    )


@router.patch("/{tender_id}", response_model=TenderView)
def change(
    tender_id: UUID,
    command: TenderUpdate,
    response: Response,
    if_match: str | None = Header(default=None),
    actor: Actor = Depends(_manager),
    session: Session = Depends(database_session, scope="function"),
):
    deadline_passed = (if_match or "").endswith('-late"')
    tender = tenders.update_tender(
        session=session,
        actor=actor,
        tender_id=tender_id,
        command=command,
        expected=expected_version(
            header=if_match,
            resource="tender",
            resource_id=tender_id,
            variant="late" if deadline_passed else "current",
        ),
        expected_deadline_passed=deadline_passed,
    )
    return _view(tender, response)


@router.get("/{tender_id}/audit-events", response_model=Page[AuditView])
def audit(
    tender_id: UUID,
    page: PageRequest = Depends(page_request),
    actor: Actor = Depends(current_actor),
    session: Session = Depends(database_session, scope="function"),
):
    scope = f"tender-audit:{tender_id}"
    rows = tenders.list_tender_audit(
        session=session,
        actor=actor,
        tender_id=tender_id,
        after=decode_cursor(page.cursor, scope=scope),
        limit=page.limit,
    )

    def project(event):
        return AuditView(
            id=event.id,
            actor_id=event.actor_id,
            action=event.action,
            entity_id=event.entity_id,
            metadata=sanitized_metadata(event.details),
            created_at=event.created_at,
        )

    return make_page(rows, limit=page.limit, scope=scope, project=project)
