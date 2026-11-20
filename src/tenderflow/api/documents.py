from contextlib import ExitStack
from urllib.parse import quote
from uuid import UUID

from anyio import CancelScope
from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from tenderflow.api.dependencies import (
    current_actor,
    get_identity_runtime,
    require_roles,
)
from tenderflow.api.pagination import (
    PageRequest,
    decode_cursor,
    make_page,
    page_request,
)
from tenderflow.api.preconditions import expected_version
from tenderflow.infrastructure.database.sessions import session_scope
from tenderflow.schemas.documents import DocumentView
from tenderflow.schemas.identity import Actor
from tenderflow.schemas.tenders import Page
from tenderflow.services import documents
from tenderflow.services.errors import ServiceError
from tenderflow.services.identity import IdentityRuntime

router = APIRouter(tags=["documents"])
_manager = require_roles("tender_manager")


class _SourceDownload(StreamingResponse):
    def __init__(self, content, *, stack, **kwargs):
        super().__init__(content, **kwargs)
        self.stack = stack

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                await run_in_threadpool(self.stack.close)


@router.post(
    "/api/v1/tenders/{tender_id}/documents",
    response_model=DocumentView,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
)
async def upload(
    tender_id: UUID,
    request: Request,
    response: Response,
    if_match: str | None = Header(default=None),
    actor: Actor = Depends(_manager),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    late = (if_match or "").endswith('-late"')
    expected = expected_version(
        header=if_match,
        resource="tender",
        resource_id=tender_id,
        variant="late" if late else "current",
    )
    if expected is None:
        raise ServiceError("precondition_required")
    async with request.form(max_files=1, max_fields=0) as form:
        file = form.get("file")
        if len(form) != 1 or not isinstance(file, UploadFile) or not file.filename:
            raise ServiceError("invalid_request")
        document = await run_in_threadpool(
            documents.upload_source,
            tender_id=tender_id,
            actor=actor,
            filename=file.filename,
            content=file.file,
            expected_version=expected,
            runtime=runtime,
            store=request.app.state.object_store,
            expected_deadline_passed=late,
        )
    response.headers["Location"] = f"/api/v1/documents/{document.id}/download"
    response.headers["Cache-Control"] = "no-store"
    return DocumentView.model_validate(document)


@router.get("/api/v1/tenders/{tender_id}/documents", response_model=Page[DocumentView])
def listing(
    tender_id: UUID,
    response: Response,
    page: PageRequest = Depends(page_request),
    actor: Actor = Depends(current_actor),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    response.headers["Cache-Control"] = "no-store"
    scope = f"documents:{tender_id}"
    with session_scope(engine=runtime.engine) as session:
        rows = documents.list_documents(
            session=session,
            tender_id=tender_id,
            actor=actor,
            after=decode_cursor(page.cursor, scope=scope),
            limit=page.limit,
        )
        return make_page(
            rows, limit=page.limit, scope=scope, project=DocumentView.model_validate
        )


@router.get("/api/v1/documents/{document_id}/download")
def download(
    document_id: UUID,
    request: Request,
    actor: Actor = Depends(current_actor),
    runtime: IdentityRuntime = Depends(get_identity_runtime),
):
    with session_scope(engine=runtime.engine) as session:
        document = documents.get_document(
            session=session, document_id=document_id, actor=actor
        )
        key, name = document.storage_key, document.original_name
    store = request.app.state.object_store
    if store is None:
        raise ServiceError("storage_unavailable")
    stack = ExitStack()
    try:
        stream = stack.enter_context(store.open(key=key))
    except Exception:
        stack.close()
        raise ServiceError("storage_unavailable") from None

    def chunks():
        while chunk := stream.read(64 * 1024):
            yield chunk

    return _SourceDownload(
        chunks(),
        stack=stack,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}",
        },
    )
