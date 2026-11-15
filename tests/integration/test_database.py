from fastapi.testclient import TestClient
from sqlalchemy import text

from tenderflow.main import create_app


def test_readiness_requires_both_migration_schemas(queue_probe) -> None:
    with TestClient(create_app(settings=queue_probe.settings)) as client:
        assert client.get("/health/ready").status_code == 200
        with queue_probe.session() as session:
            session.execute(text("DROP TABLE procrastinate_jobs CASCADE"))
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 200


def test_readiness_rejects_an_outdated_application_schema(queue_probe) -> None:
    with queue_probe.session() as session:
        session.execute(text("UPDATE alembic_version SET version_num='outdated'"))

    with TestClient(create_app(settings=queue_probe.settings)) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
