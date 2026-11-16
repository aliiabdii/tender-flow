import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tenderflow.infrastructure.auth.local import hash_secret
from tenderflow.infrastructure.database.models import (
    ActivationToken,
    AuditEvent,
    LocalCredential,
    RefreshSession,
    User,
)
from tenderflow.main import create_app
from tenderflow.schemas.identity import Actor
from tenderflow.services.identity import IdentityRuntime, bootstrap_administrator


class LocalAccounts:
    password = "synthetic passphrase for tests"

    def __init__(self, probe, client):
        self.probe = probe
        self.client = client
        result = bootstrap_administrator(
            login="administrator",
            display_name="Synthetic administrator",
            runtime=IdentityRuntime(probe.engine, probe.settings),
        )
        self.admin_id = str(result.user.id)
        self.bootstrap_token = result.activation.token.get_secret_value()
        self.activate(self.bootstrap_token)
        self.admin_pair = self.login("administrator")
        self.admin_headers = self.headers(self.admin_pair)
        manager = self.provision("manager", roles=["tender_manager"])
        self.manager_id = manager["user"]["id"]
        self.activate(manager["activation"]["token"])
        self.manager_pair = self.login("manager")

    def headers(self, pair):
        return {"Authorization": f"Bearer {pair['access_token']}"}

    def me(self, pair):
        return self.client.get("/api/v1/auth/me", headers=self.headers(pair))

    def try_login(self, login="manager", password=None):
        return self.client.post(
            "/api/v1/auth/local/login",
            json={"login": login, "password": password or self.password},
        )

    def login(self, login="manager", password=None):
        response = self.try_login(login, password)
        assert response.status_code == 200, response.text
        return response.json()

    def provision_response(self, login, roles=None):
        return self.client.post(
            "/api/v1/admin/users",
            headers=self.admin_headers,
            json={
                "login": login,
                "display_name": "Synthetic user",
                "roles": roles or ["tender_manager"],
            },
        )

    def provision(self, login, roles=None):
        response = self.provision_response(login, roles)
        assert response.status_code == 201, response.text
        return response.json()

    def activate_response(self, token, password=None):
        return self.client.post(
            "/api/v1/auth/local/activate",
            json={"token": token, "password": password or self.password},
        )

    def activate(self, token, password=None):
        response = self.activate_response(token, password)
        assert response.status_code == 204, response.text

    def update(self, user_id, **changes):
        response = self.client.patch(
            f"/api/v1/admin/users/{user_id}",
            headers=self.admin_headers,
            json=changes,
        )
        assert response.status_code == 200, response.text
        return response.json()

    def refresh_response(self, token):
        return self.client.post(
            "/api/v1/auth/local/refresh", json={"refresh_token": token}
        )

    def refresh(self, token):
        response = self.refresh_response(token)
        assert response.status_code == 200, response.text
        return response.json()

    def expire_activation(self, token):
        with self.probe.session() as session:
            session.execute(
                update(ActivationToken)
                .where(ActivationToken.token_hash == hash_secret(token))
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )

    def expire_refresh(self, token):
        with self.probe.session() as session:
            session.execute(
                update(RefreshSession)
                .where(RefreshSession.token_hash == hash_secret(token))
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )

    def changed_access_token(self, changes, algorithm="HS256"):
        settings = self.probe.settings
        payload = jwt.decode(
            self.manager_pair["access_token"],
            settings.local_signing_key.get_secret_value(),
            algorithms=["HS256"],
            audience=settings.local_token_audience,
            issuer=settings.local_token_issuer,
        )
        return jwt.encode(
            {**payload, **changes},
            settings.local_signing_key.get_secret_value(),
            algorithm=algorithm,
        )

    def persisted_data(self):
        with self.probe.session() as session:
            return json.dumps(
                [
                    list(session.execute(select(model.__table__)).mappings())
                    for model in (
                        User,
                        LocalCredential,
                        ActivationToken,
                        RefreshSession,
                        AuditEvent,
                    )
                ],
                default=str,
            )


@pytest.fixture
def local_accounts(queue_probe):
    with TestClient(create_app(settings=queue_probe.settings)) as client:
        yield LocalAccounts(queue_probe, client)


@pytest.fixture
def manager(local_accounts):
    return Actor(UUID(local_accounts.manager_id), frozenset({"tender_manager"}))


@pytest.fixture
def other_manager(local_accounts):
    user = local_accounts.provision("other-manager")["user"]
    return Actor(UUID(user["id"]), frozenset({"tender_manager"}))


@pytest.fixture
def administrator(local_accounts):
    return Actor(UUID(local_accounts.admin_id), frozenset({"admin"}))
