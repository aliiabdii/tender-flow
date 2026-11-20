from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Request, Response
from sqlalchemy.orm import Session

from tenderflow.api.dependencies import current_actor, database_session, require_roles
from tenderflow.api.pagination import (
    PageRequest,
    decode_cursor,
    make_page,
    page_request,
)
from tenderflow.schemas.identity import Actor
from tenderflow.schemas.processing import ItemView, RetryCommand, RunView
from tenderflow.schemas.tenders import Page
from tenderflow.services import processing

router = APIRouter(prefix="/api/v1/processing-runs", tags=["processing"])
_manager = require_roles("tender_manager")


@router.get("/{run_id}", response_model=RunView)
def get(
    run_id: UUID,
    response: Response,
    actor: Actor = Depends(current_actor),
    session: Session = Depends(database_session, scope="function"),
):
    response.headers["Cache-Control"] = "no-store"
    run = processing.get_run(session=session, run_id=run_id, actor=actor)
    return processing.run_view(session=session, run=run)


@router.get("/{run_id}/items", response_model=Page[ItemView])
def items(
    run_id: UUID,
    response: Response,
    page: PageRequest = Depends(page_request),
    actor: Actor = Depends(current_actor),
    session: Session = Depends(database_session, scope="function"),
):
    response.headers["Cache-Control"] = "no-store"
    scope = f"processing-items:{run_id}"
    rows = processing.list_items(
        session=session,
        run_id=run_id,
        actor=actor,
        after=decode_cursor(page.cursor, scope=scope),
        limit=page.limit,
    )
    return make_page(
        rows, limit=page.limit, scope=scope, project=ItemView.model_validate
    )


@router.post("/{run_id}/retries", response_model=RunView, status_code=202)
def retry(
    run_id: UUID,
    request: Request,
    response: Response,
    command: RetryCommand = Body(default=RetryCommand()),
    idempotency_key: str = Header(min_length=1, max_length=128),
    actor: Actor = Depends(_manager),
    session: Session = Depends(database_session, scope="function"),
):
    run = processing.retry_failed_items(
        session=session,
        run_id=run_id,
        actor=actor,
        key=idempotency_key,
        lifetime_seconds=request.app.state.settings.idempotency_seconds,
    )
    response.headers["Location"] = f"/api/v1/processing-runs/{run.id}"
    response.headers["Cache-Control"] = "no-store"
    return processing.run_view(session=session, run=run)
