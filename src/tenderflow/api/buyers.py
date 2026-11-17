from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
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
from tenderflow.schemas.tenders import BuyerCreate, BuyerUpdate, BuyerView, Page
from tenderflow.services import tenders

router = APIRouter(prefix="/api/v1/buyers", tags=["buyers"])
_manager = require_roles("tender_manager")


def _view(buyer, response):
    response.headers["ETag"] = etag(
        resource="buyer", resource_id=buyer.id, version=buyer.revision
    )
    return BuyerView.model_validate(buyer)


@router.post("", response_model=BuyerView, status_code=201)
def create(
    command: BuyerCreate,
    response: Response,
    actor: Actor = Depends(_manager),
    session: Session = Depends(database_session, scope="function"),
):
    buyer = tenders.create_buyer(session=session, actor=actor, command=command)
    response.headers["Location"] = f"/api/v1/buyers/{buyer.id}"
    return _view(buyer, response)


@router.get("", response_model=Page[BuyerView])
def listing(
    page: PageRequest = Depends(page_request),
    actor: Actor = Depends(current_actor),
    session: Session = Depends(database_session, scope="function"),
):
    rows = tenders.list_buyers(
        session=session,
        actor=actor,
        after=decode_cursor(page.cursor, scope="buyers"),
        limit=page.limit,
    )
    return make_page(
        rows, limit=page.limit, scope="buyers", project=BuyerView.model_validate
    )


@router.get("/{buyer_id}", response_model=BuyerView)
def get(
    buyer_id: UUID,
    response: Response,
    actor: Actor = Depends(current_actor),
    session: Session = Depends(database_session, scope="function"),
):
    return _view(
        tenders.get_buyer(session=session, actor=actor, buyer_id=buyer_id), response
    )


@router.patch("/{buyer_id}", response_model=BuyerView)
def change(
    buyer_id: UUID,
    command: BuyerUpdate,
    response: Response,
    if_match: str | None = Header(default=None),
    actor: Actor = Depends(_manager),
    session: Session = Depends(database_session, scope="function"),
):
    buyer = tenders.update_buyer(
        session=session,
        actor=actor,
        buyer_id=buyer_id,
        command=command,
        expected=expected_version(
            header=if_match, resource="buyer", resource_id=buyer_id
        ),
    )
    return _view(buyer, response)
