import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from sqlalchemy import text


def test_openapi_describes_required_multipart_file(client):
    upload = client.get("/openapi.json").json()["paths"][
        "/api/v1/tenders/{tender_id}/documents"
    ]["post"]
    body = upload.get("requestBody", {})
    assert body.get("required") is True
    schema = body["content"]["multipart/form-data"]["schema"]
    assert schema["required"] == ["file"]
    assert schema["properties"]["file"] == {"type": "string", "format": "binary"}


def test_upload_preserves_bytes_checksum_and_private_storage(document_uploads):
    content = b"article,quantity\r\n000123,2\r\n"
    before = document_uploads.client.get(
        f"/api/v1/tenders/{document_uploads.tender['id']}",
        headers=document_uploads.api.headers,
    ).json()
    response = document_uploads.upload(content, filename="buyer request.csv")
    assert response.status_code == 201
    result = response.json()
    assert result["checksum"] == hashlib.sha256(content).hexdigest()
    assert result["format"] == "csv"
    assert result["size_bytes"] == len(content)
    assert result["original_name"] == "buyer request.csv"
    assert "storage_key" not in result
    assert response.headers["Location"].endswith(result["id"] + "/download")
    downloaded = document_uploads.client.get(
        response.headers["Location"], headers=document_uploads.api.headers
    )
    assert downloaded.content == content
    assert downloaded.headers["Cache-Control"] == "no-store"
    assert downloaded.headers["Content-Type"].startswith("application/octet-stream")
    assert downloaded.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in downloaded.headers["Content-Disposition"]
    after = document_uploads.client.get(
        f"/api/v1/tenders/{document_uploads.tender['id']}",
        headers=document_uploads.api.headers,
    ).json()
    assert after["source_revision"] == before["source_revision"] + 1
    assert after["content_revision"] == before["content_revision"] + 1
    assert after["preparation_version"] == before["preparation_version"] + 1
    assert document_uploads.count() == 1
    assert (
        document_uploads.api.accounts.probe.scalar(
            "SELECT count(*) FROM procrastinate_jobs WHERE task_name <> 'tenderflow.recover_stalled'"
        )
        == 0
    )
    key = next(iter(document_uploads.store.objects))
    assert "buyer request" not in key and key.startswith("sources/")


def test_failed_upload_never_becomes_a_source(document_uploads):
    document_uploads.store.put_failure = True
    response = document_uploads.upload()
    assert response.status_code == 503
    assert document_uploads.count() == 0
    assert document_uploads.store.objects == {}
    assert len(document_uploads.store.deleted) == 1
    assert (
        document_uploads.api.accounts.probe.scalar(
            "SELECT count(*) FROM procrastinate_jobs"
        )
        == 0
    )


def test_database_failure_cleans_only_the_specific_uploaded_orphan(document_uploads):
    first = document_uploads.upload()
    assert first.status_code == 201
    original_keys = set(document_uploads.store.objects)
    with document_uploads.api.accounts.probe.session() as session:
        session.execute(
            text(
                "CREATE FUNCTION reject_document() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'synthetic commit failure'; END $$"
            )
        )
        session.execute(
            text(
                "CREATE CONSTRAINT TRIGGER reject_document AFTER INSERT ON documents "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_document()"
            )
        )
    assert document_uploads.upload().status_code == 500
    assert document_uploads.count() == 1
    assert set(document_uploads.store.objects) == original_keys
    assert len(document_uploads.store.deleted) == 1
    assert document_uploads.store.deleted[0] not in original_keys


@pytest.mark.parametrize("verification_unavailable", [False, True])
def test_uncertain_commit_never_deletes_a_committed_original(
    document_uploads, monkeypatch, verification_unavailable
):
    from contextlib import contextmanager

    from tenderflow.services import documents

    original = documents.session_scope
    calls = 0

    @contextmanager
    def interrupted_acknowledgement(**kwargs):
        nonlocal calls
        calls += 1
        if calls > 2 and verification_unavailable:
            raise OSError("Synthetic database outage")
        with original(**kwargs) as session:
            yield session
        if calls == 2:
            raise OSError("Synthetic lost commit acknowledgement")

    monkeypatch.setattr(documents, "session_scope", interrupted_acknowledgement)
    assert document_uploads.upload().status_code == 500
    monkeypatch.setattr(documents, "session_scope", original)
    sources = document_uploads.documents().json()["items"]
    assert len(sources) == 1
    downloaded = document_uploads.client.get(
        f"/api/v1/documents/{sources[0]['id']}/download",
        headers=document_uploads.api.headers,
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"article,quantity\n000123,2\n"


@pytest.mark.parametrize(
    "failure_message", ["http.response.start", "http.response.body"]
)
def test_download_disconnect_closes_the_original_stream(
    document_uploads, monkeypatch, failure_message
):
    import asyncio

    from starlette.requests import ClientDisconnect

    response = document_uploads.upload()
    stream = io.BytesIO(b"synthetic original")
    monkeypatch.setattr(document_uploads.store, "open", lambda **kwargs: stream)
    path = response.headers["Location"]

    async def disconnect():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                assert message["status"] == 200
            if message["type"] == failure_message:
                raise OSError("Synthetic disconnected socket")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (key.lower().encode(), value.encode())
                for key, value in document_uploads.api.headers.items()
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        with pytest.raises((ClientDisconnect, OSError)):
            await document_uploads.client.app(scope, receive, send)

    asyncio.run(disconnect())
    assert stream.closed


def test_compressed_pdf_expansion_is_bounded_even_before_a_late_text_page(
    document_uploads,
):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    for index in range(65):
        page = writer.add_blank_page(width=200, height=200)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        content = DecodedStreamObject()
        content.set_data(
            b" " * (1024 * 1024)
            if index < 64
            else b"BT /F1 12 Tf 10 10 Td (Synthetic request) Tj ET"
        )
        page[NameObject("/Contents")] = content.flate_encode()
    compressed = io.BytesIO()
    writer.write(compressed)
    assert len(compressed.getvalue()) < 100_000
    assert (
        document_uploads.upload(compressed.getvalue(), "expanded.pdf").status_code
        == 415
    )
    assert document_uploads.store.objects == {}


@pytest.mark.parametrize(
    "filename,content,status",
    [
        ("file.xls", b"legacy", 415),
        ("file.xlsm", b"macro", 415),
        ("file.csv", b"\xff\x00", 415),
        ("file.csv", b"", 415),
        ("file.csv", b'a,b\n"unterminated', 415),
        ("file.pdf", b"%PDF-1.7\nnot a pdf", 415),
        ("file.xlsx", b"not a zip", 415),
        ("file.docx", b"not a zip", 415),
        ("../../request.csv", b"a,b\n1,2", 422),
        ("file.csv", b"# tenderflow-generated-artifact\na,b\n1,2", 415),
    ],
)
def test_invalid_sources_are_rejected_without_objects(
    document_uploads, filename, content, status
):
    assert document_uploads.upload(content, filename).status_code == status
    assert document_uploads.count() == 0
    assert document_uploads.store.objects == {}


def test_document_limit_and_concurrent_stale_uploads(document_uploads):
    etag = document_uploads.etag()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: document_uploads.upload(headers={"If-Match": etag}), range(2)
            )
        )
    assert sorted(response.status_code for response in responses) == [201, 412]
    for _ in range(19):
        assert document_uploads.upload().status_code == 201
    assert document_uploads.upload().status_code == 409
    assert document_uploads.count() == 20
    assert len(document_uploads.store.objects) == 20


def test_default_25_mib_file_boundary(document_uploads):
    size = 25 * 1024 * 1024
    content = (b"article\n" + b"a\n" * (size // 2))[:size]
    assert document_uploads.upload(content).status_code == 201
    response = document_uploads.upload(content + b"x")
    assert response.status_code == 413
    assert document_uploads.count() == 1


def test_preconditions_auth_and_source_freeze(document_uploads):
    accounts = document_uploads.api.accounts
    path = document_uploads.path
    assert (
        document_uploads.client.post(
            path,
            headers=document_uploads.api.headers,
            files={"file": ("x.csv", b"a,b\n1,2")},
        ).status_code
        == 428
    )
    assert document_uploads.upload(headers={"If-Match": '"stale"'}).status_code == 412
    assert document_uploads.client.get(path).status_code == 401
    assert (
        document_uploads.client.get(path, headers=accounts.admin_headers).status_code
        == 403
    )
    response = document_uploads.upload()
    assert response.status_code == 201
    reviewer = accounts.provision("documents-reader", roles=["approver"])
    accounts.activate(reviewer["activation"]["token"])
    headers = accounts.headers(accounts.login("documents-reader"))
    assert document_uploads.client.get(path, headers=headers).status_code == 200
    assert (
        document_uploads.client.get(
            response.headers["Location"], headers=headers
        ).status_code
        == 200
    )
    assert document_uploads.upload(headers=headers).status_code == 403
    from tenderflow.services.documents import freeze_sources

    with accounts.probe.session() as session:
        freeze_sources(session=session, tender_id=document_uploads.tender_id)
    assert document_uploads.upload().status_code == 409
    assert document_uploads.count() == 1


def test_zip_validation_rejects_macros_oversized_members_and_wrong_content(
    document_uploads,
):
    for entries in [
        {"[Content_Types].xml": b"<Types/>", "xl/vbaProject.bin": b"macro"},
        {"[Content_Types].xml": b"<Types/>", "xl/huge.xml": b"x" * (51 * 1024 * 1024)},
        {"../escape.xml": b"x"},
    ]:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        assert (
            document_uploads.upload(stream.getvalue(), "file.xlsx").status_code == 415
        )
    assert document_uploads.store.objects == {}


def test_document_processing_subject_must_belong_to_run_tender(
    document_uploads, manager
):
    from tenderflow.schemas.processing import RunKind
    from tenderflow.services.errors import ServiceError
    from tenderflow.services.processing import enqueue_item, start_run

    document = document_uploads.upload().json()
    other = document_uploads.api.create()
    with pytest.raises(ServiceError, match="invalid_request"):
        with document_uploads.api.accounts.probe.session() as session:
            run = start_run(
                session=session,
                tender_id=UUID(other["id"]),
                kind=RunKind.EXTRACTION,
                actor=manager,
                key="other-tender",
            )
            enqueue_item(
                session=session,
                run_id=run.id,
                key="document",
                task_name="tests.process_item",
                document_id=UUID(document["id"]),
            )


def test_pdf_encryption_image_only_and_text_sources(document_uploads):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    image_only = io.BytesIO()
    writer.write(image_only)
    assert (
        document_uploads.upload(image_only.getvalue(), "blank.pdf").status_code == 415
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 10 10 Td (Synthetic request) Tj ET")
    page[NameObject("/Contents")] = content
    text_pdf = io.BytesIO()
    writer.write(text_pdf)
    assert document_uploads.upload(text_pdf.getvalue(), "text.pdf").status_code == 201
    writer.encrypt("synthetic-password")
    encrypted = io.BytesIO()
    writer.write(encrypted)
    assert (
        document_uploads.upload(encrypted.getvalue(), "encrypted.pdf").status_code
        == 415
    )


def test_minimal_ooxml_sources_and_generated_marker(document_uploads):
    for extension, entries in [
        (
            "xlsx",
            {
                "xl/workbook.xml": b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>',
            },
        ),
        (
            "docx",
            {
                "word/document.xml": b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/></w:body></w:document>',
            },
        ),
    ]:
        kind = (
            "spreadsheetml.sheet"
            if extension == "xlsx"
            else "wordprocessingml.document"
        )
        entries["[Content_Types].xml"] = (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f'<Override PartName="/{next(iter(entries))}" ContentType="application/vnd.openxmlformats-officedocument.{kind}.main+xml"/>'
            "</Types>"
        ).encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        assert (
            document_uploads.upload(stream.getvalue(), f"file.{extension}").status_code
            == 201
        )
        entries["docProps/custom.xml"] = (
            b'<Properties><property name="TenderFlowGeneratedArtifact"/></Properties>'
        )
        marked = io.BytesIO()
        with zipfile.ZipFile(marked, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        assert (
            document_uploads.upload(marked.getvalue(), f"file.{extension}").status_code
            == 415
        )


def test_wrong_ooxml_root_is_not_accepted_as_a_source(document_uploads):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>',
        )
        archive.writestr("xl/workbook.xml", "<unrelated/>")
    assert document_uploads.upload(content.getvalue(), "wrong.xlsx").status_code == 415


def test_missing_storage_object_returns_safe_failure_before_download_headers(
    document_uploads,
):
    response = document_uploads.upload()
    document_uploads.store.objects.clear()
    download = document_uploads.client.get(
        response.headers["Location"], headers=document_uploads.api.headers
    )
    assert download.status_code == 503
    assert download.json()["code"] == "storage_unavailable"


def test_chunked_upload_is_bounded_before_multipart_spooling(document_uploads):
    boundary = "synthetic-boundary"

    def body():
        yield (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="request.csv"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        for _ in range(402):
            yield b"a\n" * 32768
        yield f"\r\n--{boundary}--\r\n".encode()

    response = document_uploads.client.post(
        document_uploads.path,
        headers={
            **document_uploads.api.headers,
            "If-Match": document_uploads.etag(),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        content=body(),
    )
    assert response.status_code == 413
    assert document_uploads.store.objects == {}


def test_storage_call_is_outside_transactions_and_role_change_prevents_publication(
    document_uploads,
):
    from sqlalchemy import update

    from tenderflow.infrastructure.database.models import User

    original = document_uploads.store.put
    accounts = document_uploads.api.accounts

    def put(*, key, content):
        with accounts.probe.session() as session:
            session.execute(text("SET LOCAL lock_timeout='500ms'"))
            session.execute(
                update(User)
                .where(User.id == UUID(accounts.manager_id))
                .values(active=False)
            )
        return original(key=key, content=content)

    document_uploads.store.put = put
    response = document_uploads.upload()
    assert response.status_code == 403
    assert document_uploads.store.objects == {}
    with accounts.probe.session() as session:
        assert session.scalar(text("SELECT count(*) FROM documents")) == 0
