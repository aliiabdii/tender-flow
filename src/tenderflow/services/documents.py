import csv
import hashlib
import io
import logging
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import PurePath
from typing import BinaryIO
from uuid import UUID, uuid4

from defusedxml import ElementTree
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from tenderflow.infrastructure.database.models import Document, Tender
from tenderflow.infrastructure.database.sessions import session_scope
from tenderflow.infrastructure.storage import ObjectStore
from tenderflow.schemas.documents import SourceName
from tenderflow.schemas.identity import Actor
from tenderflow.services.audit import write_audit
from tenderflow.services.errors import ServiceError
from tenderflow.services.identity import IdentityRuntime, _runtime
from tenderflow.services.tenders import (
    _page,
    get_tender,
    has_passed_deadline,
    lock_tender,
    record_contribution,
)

logger = logging.getLogger(__name__)
_GENERATED = "TenderFlowGeneratedArtifact"


def _validate_format(content: BinaryIO, filename: str) -> str:
    extension = PurePath(filename).suffix.lower().removeprefix(".")
    if extension not in {"csv", "xlsx", "docx", "pdf"}:
        raise ServiceError("unsupported_document")
    try:
        if extension == "csv":
            content.seek(0)
            wrapper = io.TextIOWrapper(content, encoding="utf-8-sig", newline="")
            try:
                first = wrapper.readline()
                if (
                    not first
                    or "\x00" in first
                    or first.startswith("# tenderflow-generated-artifact")
                ):
                    raise ValueError("Invalid source")
                wrapper.seek(0)
                for row in csv.reader(wrapper, strict=True):
                    if any("\x00" in cell for cell in row):
                        raise ValueError("Invalid source")
            finally:
                wrapper.detach()
        elif extension == "pdf":
            content.seek(0)
            result = subprocess.run(
                [sys.executable, "-m", "tenderflow.services._pdf_validation"],
                stdin=content,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                raise ValueError("Unsupported or resource-intensive PDF")
        else:
            content.seek(0)
            with zipfile.ZipFile(content) as archive:
                members = archive.infolist()
                names = [entry.filename for entry in members]
                if len(names) != len(set(names)) or len(names) > 2000:
                    raise ValueError("Invalid archive")
                if sum(entry.file_size for entry in members) > 50 * 1024 * 1024:
                    raise ValueError("Expanded archive too large")
                if any(
                    entry.flag_bits & 1
                    or entry.file_size > 10 * 1024 * 1024
                    or entry.filename.startswith("/")
                    or "\\" in entry.filename
                    or ".." in PurePath(entry.filename).parts
                    or "vba" in entry.filename.lower()
                    for entry in members
                ):
                    raise ValueError("Unsupported archive")
                target = (
                    "xl/workbook.xml" if extension == "xlsx" else "word/document.xml"
                )
                if target not in names or "[Content_Types].xml" not in names:
                    raise ValueError("Missing document structure")
                expected_root = (
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}workbook"
                    if extension == "xlsx"
                    else "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document"
                )
                if ElementTree.fromstring(archive.read(target)).tag != expected_root:
                    raise ValueError("Wrong document root")
                types = ElementTree.fromstring(archive.read("[Content_Types].xml"))
                expected = (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
                    if extension == "xlsx"
                    else "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
                )
                if not any(
                    node.get("PartName") == "/" + target
                    and node.get("ContentType") == expected
                    for node in types
                ):
                    raise ValueError("Wrong document content type")
                for entry in members:
                    if entry.filename.endswith(".xml"):
                        root = ElementTree.fromstring(archive.read(entry))
                        if any(node.get("name") == _GENERATED for node in root.iter()):
                            raise ValueError("Generated artifact is not a source")
    except ServiceError:
        raise
    except Exception:
        raise ServiceError("unsupported_document") from None
    finally:
        content.seek(0)
    return extension


def _eligible(session, tender, maximum):
    if tender.sources_frozen:
        raise ServiceError("sources_frozen")
    count = session.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.tender_id == tender.id)
    )
    if count >= maximum:
        raise ServiceError("document_limit")


def freeze_sources(*, session: Session, tender_id: UUID) -> None:
    tender = session.get(
        Tender, tender_id, with_for_update=True, populate_existing=True
    )
    if tender is None:
        raise ServiceError("tender_not_found")
    tender.sources_frozen = True


def _remove_unpublished_source(*, runtime, store, tender_id, key):
    try:
        with session_scope(engine=runtime.engine) as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            # Wait for any ambiguous publication commit before checking visibility.
            session.get(Tender, tender_id, with_for_update=True)
            published = session.scalar(
                select(Document.id).where(Document.storage_key == key)
            )
        if published is None:
            store.delete(key=key)
    except Exception:
        logger.error("Source orphan reconciliation required", extra={"object_key": key})


def upload_source(
    *,
    tender_id: UUID,
    actor: Actor,
    filename: str,
    content: BinaryIO,
    expected_version: int | None,
    runtime: IdentityRuntime | None = None,
    store: ObjectStore | None = None,
    expected_deadline_passed: bool | None = None,
) -> Document:
    runtime = _runtime(runtime)
    if store is None:
        raise ServiceError("storage_unavailable")
    try:
        SourceName(filename=filename)
    except ValidationError:
        raise ServiceError("invalid_request") from None
    with session_scope(engine=runtime.engine) as session:
        tender = lock_tender(
            session=session, actor=actor, tender_id=tender_id, expected=expected_version
        )
        _eligible(session, tender, runtime.settings.max_documents_per_tender)
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = content.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > runtime.settings.max_upload_bytes:
                raise ServiceError("upload_too_large")
            digest.update(chunk)
            spool.write(chunk)
        format_ = _validate_format(spool, filename)
        key = f"sources/{tender_id}/{uuid4()}"
        try:
            try:
                store.put(key=key, content=spool)
            except Exception:
                raise ServiceError("storage_unavailable") from None
            with session_scope(engine=runtime.engine) as session:
                tender = lock_tender(
                    session=session,
                    actor=actor,
                    tender_id=tender_id,
                    expected=expected_version,
                )
                _eligible(session, tender, runtime.settings.max_documents_per_tender)
                if (
                    expected_deadline_passed is not None
                    and expected_deadline_passed != has_passed_deadline(tender)
                ):
                    raise ServiceError("precondition_failed")
                tender.source_revision += 1
                tender.preparation_version += 1
                tender.content_revision += 1
                tender.updated_at = datetime.now(UTC)
                document = Document(
                    id=uuid4(),
                    tender_id=tender_id,
                    original_name=filename,
                    format=format_,
                    checksum=digest.hexdigest(),
                    storage_key=key,
                    size_bytes=size,
                    source_revision=tender.source_revision,
                    created_by=actor.id,
                )
                session.add(document)
                session.flush()
                record_contribution(session=session, tender_id=tender_id, actor=actor)
                write_audit(
                    session=session,
                    actor=actor,
                    action="document.uploaded",
                    entity_id=tender_id,
                    metadata={
                        "document_id": str(document.id),
                        "source_revision": tender.source_revision,
                    },
                )
                session.expunge(document)
            return document
        except Exception:
            _remove_unpublished_source(
                runtime=runtime, store=store, tender_id=tender_id, key=key
            )
            raise


def get_document(*, session: Session, document_id: UUID, actor: Actor) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise ServiceError("document_not_found")
    get_tender(session=session, actor=actor, tender_id=document.tender_id)
    return document


def list_documents(*, session: Session, tender_id: UUID, actor: Actor, after, limit):
    get_tender(session=session, actor=actor, tender_id=tender_id)
    return _page(
        session,
        select(Document).where(Document.tender_id == tender_id),
        Document,
        after,
        limit,
    )
