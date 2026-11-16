import io
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from tenderflow.config import Settings
from tenderflow.main import create_app

TENANT_ID = "11111111-1111-4111-8111-111111111111"
OBJECT_ID = "22222222-2222-4222-8222-222222222222"
API_ID = "33333333-3333-4333-8333-333333333333"
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
JWKS_URI = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"
SCOPE = f"api://{API_ID}/access_as_user"


@pytest.fixture(scope="session")
def entra_keys():
    return [
        rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2)
    ]


class EntraLogins:
    def __init__(self, accounts, client, keys):
        self.accounts = accounts
        self.client = client
        self.keys = keys
        self.key_number = 0
        self.fetches = []
        self.unavailable = False
        self.jwk_changes = {}

    def jwks_open(self, opener, request, *, timeout):
        assert request.full_url == JWKS_URI
        assert timeout <= 5
        self.fetches.append(request.full_url)
        if self.unavailable:
            raise urllib.error.URLError("Synthetic key-service outage")
        key = jwt.algorithms.RSAAlgorithm.to_jwk(
            self.keys[self.key_number].public_key(), as_dict=True
        )
        return io.BytesIO(
            json.dumps(
                {
                    "keys": [
                        {
                            **key,
                            "kid": f"key-{self.key_number}",
                            "use": "sig",
                            **self.jwk_changes,
                        }
                    ]
                }
            ).encode()
        )

    def token(self, changes=None, *, remove=(), headers=None, key_number=None):
        now = datetime.now(UTC)
        claims = {
            "iss": ISSUER,
            "aud": API_ID,
            "tid": TENANT_ID,
            "oid": OBJECT_ID,
            "sub": "synthetic-pairwise-subject",
            "azp": "44444444-4444-4444-8444-444444444444",
            "ver": "2.0",
            "scp": "access_as_user",
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=10),
            "preferred_username": "manager",
            "email": "manager",
            "roles": ["admin"],
        }
        claims.update(changes or {})
        for name in remove:
            claims.pop(name, None)
        number = self.key_number if key_number is None else key_number
        return jwt.encode(
            claims,
            self.keys[number],
            algorithm="RS256",
            headers={"kid": f"key-{number}", **(headers or {})},
        )

    def me(self, token=None):
        return self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token or self.token()}"},
        )

    def link_response(
        self, *, user_id=None, tenant_id=TENANT_ID, object_id=OBJECT_ID, headers=None
    ):
        return self.client.post(
            f"/api/v1/admin/users/{user_id or self.accounts.manager_id}/entra-identities",
            headers=headers if headers is not None else self.accounts.admin_headers,
            json={"tenant_id": tenant_id, "object_id": object_id},
        )

    def link(self, **kwargs):
        response = self.link_response(**kwargs)
        assert response.status_code == 201, response.text
        return response.json()

    def unlink(self, identity_id, *, user_id=None, headers=None):
        return self.client.delete(
            f"/api/v1/admin/users/{user_id or self.accounts.manager_id}/entra-identities/{identity_id}",
            headers=headers if headers is not None else self.accounts.admin_headers,
        )


@pytest.fixture
def entra_logins(local_accounts, entra_keys, monkeypatch):
    settings = Settings.model_validate(
        {
            **local_accounts.probe.settings.model_dump(),
            "entra_tenant_id": TENANT_ID,
            "entra_audience": API_ID,
            "entra_required_scope": SCOPE,
        }
    )
    with TestClient(create_app(settings=settings)) as client:
        logins = EntraLogins(local_accounts, client, entra_keys)
        monkeypatch.setattr(
            urllib.request.OpenerDirector,
            "open",
            lambda opener, request, timeout: logins.jwks_open(
                opener, request, timeout=timeout
            ),
        )
        yield logins


@pytest.fixture
def linked_logins(entra_logins):
    entra_logins.identity = entra_logins.link()
    return entra_logins
