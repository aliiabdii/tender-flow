import socket

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tenderflow.config import Settings
from tenderflow.main import create_app


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_without_database_configuration(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_app_factory_validates_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENDERFLOW_MAX_UPLOAD_BYTES", "0")

    with pytest.raises(ValidationError):
        create_app()


def test_app_factory_uses_explicit_settings(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setenv("TENDERFLOW_MAX_UPLOAD_BYTES", "0")

    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/health/live").status_code == 200


def test_app_creation_and_liveness_do_not_connect_to_external_services(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def reject_connection(*args: object, **kwargs: object) -> None:
        pytest.fail("Liveness must not connect to external services")

    monkeypatch.setattr(socket, "create_connection", reject_connection)
    monkeypatch.setattr(socket.socket, "connect", reject_connection)

    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/health/live").status_code == 200


def test_entra_configuration_does_not_fetch_keys_during_startup_or_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_connection(*args: object, **kwargs: object) -> None:
        pytest.fail("App startup and liveness must not fetch Entra keys")

    monkeypatch.setattr(socket.socket, "connect", reject_connection)
    settings = Settings(
        entra_tenant_id="11111111-1111-4111-8111-111111111111",
        entra_audience="33333333-3333-4333-8333-333333333333",
        entra_required_scope="api://synthetic/access_as_user",
    )
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/health/live").status_code == 200
