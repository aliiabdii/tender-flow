import base64
import binascii
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from tenderflow.schemas.tenders import Page
from tenderflow.services.errors import ServiceError


class Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    scope: str
    created_at: AwareDatetime
    id: UUID


@dataclass(frozen=True)
class PageRequest:
    limit: int
    cursor: str | None


def page_request(
    request: Request,
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None, max_length=1024),
) -> PageRequest:
    settings = request.app.state.settings
    limit = settings.default_page_size if limit is None else limit
    if limit > settings.max_page_size:
        raise ServiceError("invalid_request")
    return PageRequest(limit, cursor)


def decode_cursor(token: str | None, *, scope: str):
    if token is None:
        return None
    try:
        raw = base64.b64decode(
            token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
        )
        cursor = Cursor.model_validate_json(raw)
        if cursor.scope != scope:
            raise ValueError("Wrong cursor scope")
        return cursor.created_at, cursor.id
    except (ValueError, ValidationError, binascii.Error):
        raise ServiceError("invalid_cursor") from None


def make_page(rows, *, limit: int, scope: str, project) -> Page:
    items = [project(row) for row in rows[:limit]]
    token = None
    if len(rows) > limit:
        last = rows[limit - 1]
        cursor = Cursor(scope=scope, created_at=last.created_at, id=last.id)
        token = (
            base64.urlsafe_b64encode(cursor.model_dump_json().encode())
            .decode()
            .rstrip("=")
        )
    return Page(items=items, next_cursor=token)
