from uuid import UUID, uuid4

import pytest
from sqlalchemy import select


class ProcessingItems:
    def __init__(self, tenders_api, manager):
        self.api = tenders_api
        self.probe = tenders_api.accounts.probe
        self.actor = manager
        self.tender_id = UUID(tenders_api.create()["id"])

    def queued(self, count=1, key=None, kind="extraction"):
        from tenderflow.schemas.processing import RunKind
        from tenderflow.services.processing import enqueue_item, start_run

        with self.probe.session() as session:
            run = start_run(
                session=session,
                tender_id=self.tender_id,
                kind=RunKind(kind),
                actor=self.actor,
                key=key or str(uuid4()),
            )
            ids = [
                enqueue_item(
                    session=session,
                    run_id=run.id,
                    key=str(index),
                    task_name="tests.process_item",
                ).id
                for index in range(count)
            ]
            return run.id, ids

    def inputs(self, item_id):
        from tenderflow.infrastructure.database.models import ProcessingItem
        from tenderflow.schemas.processing import InputVersions

        with self.probe.session() as session:
            return InputVersions.model_validate(
                session.get(ProcessingItem, item_id).expected_inputs
            )

    def complete(self, item_id, *, failed=False, result_id=None, expected=None):
        from tenderflow.schemas.processing import ItemResult
        from tenderflow.services.processing import publish_item

        with self.probe.session() as session:
            return publish_item(
                session=session,
                item_id=item_id,
                expected_inputs=expected or self.inputs(item_id),
                result=ItemResult(
                    status="failed" if failed else "succeeded",
                    error_code="upstream_unavailable" if failed else None,
                    result_id=result_id,
                ),
            )

    def get(self, run_id):
        return self.api.client.get(
            f"/api/v1/processing-runs/{run_id}", headers=self.api.headers
        )

    def retry(self, run_id, key="retry-operation", **changes):
        return self.api.client.post(
            f"/api/v1/processing-runs/{run_id}/retries",
            headers={**self.api.headers, "Idempotency-Key": key},
            **changes,
        )

    def items(self, run_id):
        from tenderflow.infrastructure.database.models import ProcessingItem

        with self.probe.session() as session:
            return [
                (item.id, item.state, item.retry_of_item_id)
                for item in session.scalars(
                    select(ProcessingItem).where(ProcessingItem.run_id == run_id)
                )
            ]


@pytest.fixture
def processing_items(tenders_api, manager):
    return ProcessingItems(tenders_api, manager)
