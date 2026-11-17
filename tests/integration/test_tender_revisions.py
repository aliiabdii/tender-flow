from uuid import UUID, uuid4

import pytest
from sqlalchemy import select


def test_preparation_helper_increments_versions_and_keeps_contribution_unique(
    tenders_api, manager
) -> None:
    from tenderflow.infrastructure.database.models.tenders import (
        Tender,
        TenderContributor,
    )
    from tenderflow.services.tenders import lock_tender, touch_preparation

    tender = tenders_api.create()
    with tenders_api.accounts.probe.session() as session:
        stored = lock_tender(
            session=session, actor=manager, tender_id=UUID(tender["id"]), expected=1
        )
        touch_preparation(session=session, tender=stored, actor=manager)
        touch_preparation(session=session, tender=stored, actor=manager)
    with tenders_api.accounts.probe.session() as session:
        stored = session.get(Tender, UUID(tender["id"]))
        assert (
            stored.content_revision,
            stored.preparation_version,
            stored.source_revision,
        ) == (3, 3, 1)
        assert (
            len(
                session.scalars(
                    select(TenderContributor).where(
                        TenderContributor.tender_id == stored.id
                    )
                ).all()
            )
            == 1
        )


def test_idempotency_record_and_result_roll_back_together(tenders_api, manager) -> None:
    from tenderflow.infrastructure.database.models.tenders import Buyer
    from tenderflow.services.tenders import idempotent_command

    result_id = uuid4()

    def execute(session):
        session.add(Buyer(id=result_id, name="Rolled back", customer_number="000001"))
        return result_id

    with pytest.raises(RuntimeError):
        with tenders_api.accounts.probe.session() as session:
            idempotent_command(
                session=session,
                actor=manager,
                scope="test.create",
                key="rollback",
                request_hash="a" * 64,
                execute=lambda: execute(session),
            )
            raise RuntimeError("Rollback")
    with tenders_api.accounts.probe.session() as session:
        assert session.get(Buyer, result_id) is None
        observed = idempotent_command(
            session=session,
            actor=manager,
            scope="test.create",
            key="rollback",
            request_hash="b" * 64,
            execute=lambda: execute(session),
        )
        assert observed == result_id


def test_expired_idempotency_key_can_be_reused(tenders_api) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from tenderflow.infrastructure.database.models.tenders import IdempotencyKey

    first = tenders_api.create_response(key="expired-key")
    with tenders_api.accounts.probe.session() as session:
        session.execute(
            update(IdempotencyKey).values(
                expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
    second = tenders_api.create_response(key="expired-key", title="New operation")
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
