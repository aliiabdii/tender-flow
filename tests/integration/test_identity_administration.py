import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient
from sqlalchemy import select

from tenderflow.infrastructure.database.models import AuditEvent
from tenderflow.main import create_app


def test_bootstrap_cli_emits_one_time_activation_and_cannot_replace_admin(
    queue_probe,
) -> None:
    env = {
        **os.environ,
        "TENDERFLOW_DATABASE_URL": queue_probe.settings.database_url.get_secret_value(),
    }
    command = [
        sys.executable,
        "-m",
        "tenderflow.infrastructure.auth.bootstrap",
        "--login",
        "first-admin",
        "--display-name",
        "Synthetic administrator",
    ]
    first = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result["user"]["roles"] == ["admin"]
    token = result["activation"]["token"]
    assert token not in first.stderr
    with TestClient(create_app(settings=queue_probe.settings)) as client:
        assert (
            client.post(
                "/api/v1/auth/local/activate",
                json={"token": token, "password": "synthetic administrator passphrase"},
            ).status_code
            == 204
        )
    second = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=15
    )
    assert second.returncode != 0
    assert "administrator_exists" in second.stderr
    assert token not in second.stdout + second.stderr


def test_ip_throttle_cannot_be_bypassed_by_changing_login(local_accounts) -> None:
    settings = local_accounts.probe.settings.model_copy(
        update={"login_ip_max_attempts": 2}
    )
    with TestClient(create_app(settings=settings)) as client:
        statuses = [
            client.post(
                "/api/v1/auth/local/login",
                json={"login": f"unknown-{i}", "password": "incorrect-password"},
            ).status_code
            for i in range(3)
        ]
    assert statuses == [401, 401, 429]


def test_user_without_local_access_has_no_activation_secret(local_accounts) -> None:
    response = local_accounts.client.post(
        "/api/v1/admin/users",
        headers=local_accounts.admin_headers,
        json={
            "login": "external-only",
            "display_name": "Synthetic",
            "roles": [],
            "local_access": False,
        },
    )
    assert response.status_code == 201
    assert response.json()["activation"] is None
    assert local_accounts.try_login("external-only").status_code == 401


def test_admin_audit_records_disabling_state(local_accounts) -> None:
    local_accounts.update(local_accounts.manager_id, active=False)
    with local_accounts.probe.session() as session:
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "user.updated")
        )
        assert event.actor_id is not None
        assert event.details["status"] == "INACTIVE"


def test_readiness_requires_local_signing_configuration(queue_probe) -> None:
    settings = queue_probe.settings.model_copy(update={"local_signing_key": None})
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503


def test_expired_password_reset_does_not_restore_local_access(local_accounts) -> None:
    response = local_accounts.client.post(
        f"/api/v1/admin/users/{local_accounts.manager_id}/password-resets",
        headers=local_accounts.admin_headers,
    )
    token = response.json()["token"]
    local_accounts.expire_activation(token)
    assert local_accounts.activate_response(token).status_code == 401
    assert local_accounts.try_login("manager").status_code == 401
