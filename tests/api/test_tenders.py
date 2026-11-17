from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select


def test_manager_creates_a_buyer(local_accounts) -> None:
    response = local_accounts.client.post(
        "/api/v1/buyers",
        headers=local_accounts.headers(local_accounts.manager_pair),
        json={"name": "Synthetic buyer", "customer_number": "000123"},
    )
    assert response.status_code == 201
    assert response.json()["customer_number"] == "000123"
    assert response.headers["Location"].endswith(response.json()["id"])
    assert response.headers["ETag"]


@pytest.mark.parametrize("resource", ["buyers", "tenders"])
def test_failed_commit_never_reports_success(tenders_api, resource) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from tenderflow.main import create_app

    with tenders_api.accounts.probe.session() as session:
        session.execute(
            text(
                "CREATE FUNCTION reject_registration() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'synthetic commit failure'; END $$"
            )
        )
        session.execute(
            text(
                f"CREATE CONSTRAINT TRIGGER reject_registration AFTER INSERT ON {resource} "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION reject_registration()"
            )
        )
    command = (
        {"name": "Uncommitted buyer", "customer_number": "000321"}
        if resource == "buyers"
        else {"buyer_id": tenders_api.buyer["id"], "title": "Uncommitted tender"}
    )
    with TestClient(
        create_app(settings=tenders_api.accounts.probe.settings),
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            f"/api/v1/{resource}", headers=tenders_api.headers, json=command
        )
    assert response.status_code == 500
    listing = tenders_api.client.get(
        f"/api/v1/{resource}", headers=tenders_api.headers
    ).json()["items"]
    assert len(listing) == (1 if resource == "buyers" else 0)


def test_customer_number_is_an_explicit_snapshot(tenders_api) -> None:
    tender = tenders_api.create()
    assert tender["pricing_customer_number"] == "000123"
    buyer = tenders_api.client.patch(
        f"/api/v1/buyers/{tenders_api.buyer['id']}",
        headers={**tenders_api.headers, "If-Match": tenders_api.buyer_etag},
        json={"customer_number": "009999"},
    )
    assert buyer.status_code == 200
    assert tenders_api.get(tender["id"]).json()["pricing_customer_number"] == "000123"
    assert tenders_api.create()["pricing_customer_number"] == "009999"
    assert (
        tenders_api.create(pricing_customer_number="000777")["pricing_customer_number"]
        == "000777"
    )


def test_tender_updates_require_current_resource_specific_versions(tenders_api) -> None:
    tender = tenders_api.create()
    first = tenders_api.get(tender["id"])
    assert tenders_api.patch(tender["id"], title="Changed").status_code == 428
    updated = tenders_api.patch(
        tender["id"], etag=first.headers["ETag"], title="Changed"
    )
    assert updated.status_code == 200
    assert updated.json()["content_revision"] == 2
    assert updated.json()["preparation_version"] == 2
    assert updated.headers["ETag"] != first.headers["ETag"]
    assert (
        tenders_api.patch(
            tender["id"], etag=first.headers["ETag"], title="Lost edit"
        ).status_code
        == 412
    )
    other = tenders_api.create()
    assert (
        tenders_api.patch(
            other["id"], etag=updated.headers["ETag"], title="Wrong resource"
        ).status_code
        == 412
    )
    assert tenders_api.get(tender["id"]).json()["title"] == "Changed"


@pytest.mark.parametrize("header", ["*", 'W/"weak"', '"unrelated"', '"a", "b"'])
def test_preconditions_do_not_accept_blind_or_unsupported_validators(
    tenders_api, header
) -> None:
    tender = tenders_api.create()
    assert (
        tenders_api.patch(tender["id"], etag=header, title="Unexpected").status_code
        == 412
    )


def test_amendment_starts_fresh_without_rewriting_history(tenders_api) -> None:
    old = tenders_api.create()
    first = tenders_api.get(old["id"])
    assert (
        tenders_api.patch(
            old["id"], etag=first.headers["ETag"], title="Prepared request"
        ).status_code
        == 200
    )
    before = tenders_api.get(old["id"]).json()
    new = tenders_api.create(supersedes_tender_id=old["id"])
    assert new["supersedes_tender_id"] == old["id"]
    assert new["status"] == "draft"
    assert (
        new["content_revision"]
        == new["preparation_version"]
        == new["source_revision"]
        == 1
    )
    assert tenders_api.get(old["id"]).json() == before
    assert (
        tenders_api.patch(
            old["id"],
            etag=tenders_api.get(old["id"]).headers["ETag"],
            supersedes_tender_id=new["id"],
        ).status_code
        == 422
    )


def test_amendment_requires_an_existing_tender(tenders_api) -> None:
    assert (
        tenders_api.create_response(supersedes_tender_id=str(uuid4())).status_code
        == 404
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"created_by": str(uuid4())},
        {"content_revision": 30},
        {"status": "approved"},
        {"buyer_id": str(uuid4())},
        {"source_revision": 50},
        {"title": None},
        {},
    ],
)
def test_patch_does_not_expose_internal_or_immutable_fields(
    tenders_api, changes
) -> None:
    tender = tenders_api.create()
    etag = tenders_api.get(tender["id"]).headers["ETag"]
    assert tenders_api.patch(tender["id"], etag=etag, **changes).status_code == 422
    assert tenders_api.get(tender["id"]).json()["content_revision"] == 1


def test_deadlines_require_offsets_but_expired_deadlines_do_not_block_edits(
    tenders_api,
) -> None:
    assert (
        tenders_api.create_response(
            submission_deadline="2020-01-01T10:00:00"
        ).status_code
        == 422
    )
    tender = tenders_api.create(submission_deadline="2020-01-01T10:00:00+01:00")
    assert tender["warnings"] == ["submission_deadline_passed"]
    assert datetime.fromisoformat(tender["submission_deadline"]).tzinfo is not None
    etag = tenders_api.get(tender["id"]).headers["ETag"]
    response = tenders_api.patch(tender["id"], etag=etag, title="Continue late")
    assert response.status_code == 200
    assert response.json()["warnings"] == ["submission_deadline_passed"]
    response = tenders_api.patch(
        tender["id"], etag=response.headers["ETag"], submission_deadline=None
    )
    assert response.status_code == 200
    assert response.json()["warnings"] == []


def test_deadline_transition_changes_etag_without_revising_content(
    tenders_api, monkeypatch
) -> None:
    from datetime import timedelta

    from tenderflow.services import tenders

    deadline = datetime(2030, 1, 1, tzinfo=UTC)

    class Clock(datetime):
        instant = deadline - timedelta(seconds=1)

        @classmethod
        def now(cls, tz=None):
            return cls.instant.astimezone(tz)

    monkeypatch.setattr(tenders, "datetime", Clock)
    tender = tenders_api.create(submission_deadline=deadline.isoformat())
    before = tenders_api.get(tender["id"])
    assert before.json()["warnings"] == []
    Clock.instant = deadline
    after = tenders_api.get(tender["id"])
    assert after.json()["warnings"] == ["submission_deadline_passed"]
    assert before.json()["content_revision"] == after.json()["content_revision"] == 1
    assert before.headers["ETag"] != after.headers["ETag"]
    assert (
        tenders_api.patch(
            tender["id"], etag=before.headers["ETag"], title="Stale representation"
        ).status_code
        == 412
    )
    updated = tenders_api.patch(
        tender["id"], etag=after.headers["ETag"], title="Continue after deadline"
    )
    assert updated.status_code == 200
    assert updated.json()["warnings"] == ["submission_deadline_passed"]
    assert updated.json()["content_revision"] == 2


def test_offset_deadline_representation_stays_stable_for_the_same_etag(
    tenders_api,
) -> None:
    created = tenders_api.create_response(
        submission_deadline="2020-01-01T10:00:00+01:00"
    )
    fetched = tenders_api.get(created.json()["id"])
    assert created.headers["ETag"] == fetched.headers["ETag"]
    assert created.json() == fetched.json()


def test_idempotent_creation_reuses_a_result_and_rejects_changed_payload(
    tenders_api,
) -> None:
    first = tenders_api.create_response(key="synthetic-operation")
    same = tenders_api.create_response(key="synthetic-operation")
    assert first.status_code == same.status_code == 201
    assert first.json()["id"] == same.json()["id"]
    assert (
        tenders_api.create_response(
            key="synthetic-operation", title="Different request"
        ).status_code
        == 409
    )
    page = tenders_api.client.get("/api/v1/tenders", headers=tenders_api.headers).json()
    assert len(page["items"]) == 1


def test_concurrent_edits_do_not_both_succeed_against_one_revision(tenders_api) -> None:
    tender = tenders_api.create()
    etag = tenders_api.get(tender["id"]).headers["ETag"]
    other = tenders_api.accounts.provision("concurrent-editor")
    tenders_api.accounts.activate(other["activation"]["token"])
    other_headers = tenders_api.accounts.headers(
        tenders_api.accounts.login("concurrent-editor")
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda headers: tenders_api.patch(
                    tender["id"], etag=etag, headers=headers, title="Concurrent edit"
                ),
                [tenders_api.headers, other_headers],
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 412]
    assert tenders_api.get(tender["id"]).json()["content_revision"] == 2


def test_concurrent_idempotent_creations_publish_once(tenders_api) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: tenders_api.create_response(key="concurrent"), range(2))
        )
    assert all(response.status_code == 201 for response in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]


def test_reading_does_not_record_contributions_and_edits_retain_all_actors(
    tenders_api,
) -> None:
    from tenderflow.infrastructure.database.models.tenders import TenderContributor

    tender = tenders_api.create()
    other = tenders_api.accounts.provision("second-manager")
    tenders_api.accounts.activate(other["activation"]["token"])
    headers = tenders_api.accounts.headers(tenders_api.accounts.login("second-manager"))
    response = tenders_api.client.get(
        f"/api/v1/tenders/{tender['id']}", headers=headers
    )
    with tenders_api.accounts.probe.session() as session:
        assert session.scalars(
            select(TenderContributor.user_id).where(
                TenderContributor.tender_id == UUID(tender["id"])
            )
        ).all() == [UUID(tenders_api.accounts.manager_id)]
    edited = tenders_api.patch(
        tender["id"],
        etag=response.headers["ETag"],
        headers=headers,
        title="Handed over",
    )
    assert edited.status_code == 200
    with tenders_api.accounts.probe.session() as session:
        contributors = set(
            session.scalars(
                select(TenderContributor.user_id).where(
                    TenderContributor.tender_id == UUID(tender["id"])
                )
            )
        )
        assert contributors == {
            UUID(tenders_api.accounts.manager_id),
            UUID(other["user"]["id"]),
        }


def test_audit_history_is_authorized_paginated_and_sanitized(tenders_api) -> None:
    tender = tenders_api.create(
        title="do-not-audit-raw-title", pricing_customer_number="do-not-audit-customer"
    )
    path = f"/api/v1/tenders/{tender['id']}/audit-events"
    assert tenders_api.client.get(path).status_code == 401
    first = tenders_api.get(tender["id"])
    tenders_api.patch(tender["id"], etag=first.headers["ETag"], title="Edited")
    page = tenders_api.client.get(
        path, headers=tenders_api.headers, params={"limit": 1}
    )
    assert page.status_code == 200
    assert len(page.json()["items"]) == 1
    assert page.json()["next_cursor"]
    next_page = tenders_api.client.get(
        path, headers=tenders_api.headers, params={"cursor": page.json()["next_cursor"]}
    )
    assert len(next_page.json()["items"]) == 1
    assert next_page.json()["next_cursor"] is None
    assert "do-not-audit" not in page.text + next_page.text
    assert next_page.json()["items"][0]["metadata"]["content_revision"] == 2


def test_audit_read_projects_only_valid_safe_metadata(tenders_api) -> None:
    from tenderflow.infrastructure.database.models import AuditEvent

    tender = tenders_api.create()
    with tenders_api.accounts.probe.session() as session:
        session.add(
            AuditEvent(
                actor_id=UUID(tenders_api.accounts.manager_id),
                action="tender.synthetic",
                entity_id=UUID(tender["id"]),
                details={
                    "title": "do-not-expose-this",
                    "roles": [{}],
                    "status": [],
                    "reason_code": {},
                    "tender_id": ["do-not-expose-this"],
                    "content_revision": 1,
                },
            )
        )
    response = tenders_api.client.get(
        f"/api/v1/tenders/{tender['id']}/audit-events", headers=tenders_api.headers
    )
    assert response.status_code == 200
    event = next(
        item
        for item in response.json()["items"]
        if item["action"] == "tender.synthetic"
    )
    assert event["metadata"] == {"content_revision": 1}
    assert "do-not-expose-this" not in response.text


def test_managers_and_approvers_read_but_only_managers_mutate(tenders_api) -> None:
    tender = tenders_api.create()
    account = tenders_api.accounts.provision("reviewer", roles=["approver"])
    tenders_api.accounts.activate(account["activation"]["token"])
    reviewer = tenders_api.accounts.headers(tenders_api.accounts.login("reviewer"))
    for path in [
        "/api/v1/buyers",
        "/api/v1/tenders",
        f"/api/v1/tenders/{tender['id']}",
        f"/api/v1/tenders/{tender['id']}/audit-events",
    ]:
        assert tenders_api.client.get(path, headers=reviewer).status_code == 200
    assert (
        tenders_api.patch(
            tender["id"],
            etag=tenders_api.get(tender["id"]).headers["ETag"],
            headers=reviewer,
            title="Forbidden",
        ).status_code
        == 403
    )
    assert (
        tenders_api.client.get(
            "/api/v1/tenders", headers=tenders_api.accounts.admin_headers
        ).status_code
        == 403
    )
    assert tenders_api.client.get("/api/v1/tenders").status_code == 401


def test_cursor_pages_use_stable_tie_breakers_and_enforce_100_200_limits(
    tenders_api,
) -> None:
    from tenderflow.infrastructure.database.models.tenders import Buyer

    with tenders_api.accounts.probe.session() as session:
        session.add_all(
            [
                Buyer(
                    name=f"Synthetic {i}",
                    customer_number=f"{i:06}",
                    created_at=datetime(2030, 1, 1, tzinfo=UTC),
                )
                for i in range(204)
            ]
        )
    first = tenders_api.client.get("/api/v1/buyers", headers=tenders_api.headers).json()
    assert len(first["items"]) == 100
    ids = {item["id"] for item in first["items"]}
    second = tenders_api.client.get(
        "/api/v1/buyers",
        headers=tenders_api.headers,
        params={"cursor": first["next_cursor"], "limit": 200},
    ).json()
    assert len(second["items"]) == 105
    assert not ids.intersection(item["id"] for item in second["items"])
    assert second["next_cursor"] is None
    for limit in [0, 201]:
        assert (
            tenders_api.client.get(
                "/api/v1/buyers", headers=tenders_api.headers, params={"limit": limit}
            ).status_code
            == 422
        )
    assert (
        tenders_api.client.get(
            "/api/v1/tenders",
            headers=tenders_api.headers,
            params={"cursor": first["next_cursor"]},
        ).status_code
        == 422
    )
    assert (
        tenders_api.client.get(
            "/api/v1/buyers",
            headers=tenders_api.headers,
            params={"cursor": "not-a-cursor"},
        ).status_code
        == 422
    )
