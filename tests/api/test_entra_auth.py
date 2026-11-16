import json
import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tenderflow.infrastructure.database.models import AuditEvent
from tenderflow.main import create_app
from tests.support.entra import API_ID, JWKS_URI, OBJECT_ID, SCOPE, TENANT_ID


def test_administrator_can_explicitly_link_an_entra_identity(entra_logins) -> None:
    identity = entra_logins.link()
    assert identity["user_id"] == entra_logins.accounts.manager_id
    assert identity["object_id"] == OBJECT_ID
    assert identity["id"]


def test_two_login_methods_resolve_to_one_user(linked_logins) -> None:
    local = linked_logins.accounts.me(linked_logins.accounts.manager_pair)
    external = linked_logins.me()
    assert external.status_code == 200
    assert external.json()["id"] == local.json()["id"]
    assert external.json()["roles"] == ["tender_manager"]
    assert (
        linked_logins.me(linked_logins.token({"oid": str(uuid4())})).status_code == 401
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "https://graph.microsoft.com"},
        {"aud": [API_ID, "another-audience"]},
        {"tid": str(uuid4())},
        {"iss": "https://untrusted.invalid/tenant/v2.0"},
        {"scp": "different_scope"},
        {"scp": "access_as_user.extra"},
        {"scp": ["access_as_user"]},
        {"exp": 1},
        {"nbf": 9_999_999_999},
        {"iat": 9_999_999_999},
        {"ver": "1.0"},
        {"idtyp": "app"},
        {"oid": "not-an-object-id"},
        {"tid": []},
    ],
)
def test_invalid_entra_access_tokens_are_rejected(linked_logins, changes) -> None:
    token = linked_logins.token(changes)
    assert linked_logins.me(token).status_code == 401


@pytest.mark.parametrize("removed", ["scp", "oid", "tid", "exp", "nbf", "iat", "sub"])
def test_required_user_access_claims_cannot_be_omitted(linked_logins, removed) -> None:
    assert linked_logins.me(linked_logins.token(remove=[removed])).status_code == 401


def test_id_tokens_are_not_accepted_as_api_access_tokens(linked_logins) -> None:
    token = linked_logins.token(
        {"aud": "44444444-4444-4444-8444-444444444444", "nonce": "synthetic-nonce"},
        remove=["scp"],
    )
    assert linked_logins.me(token).status_code == 401


def test_unlinked_subject_is_not_created_or_linked_by_username(entra_logins) -> None:
    assert entra_logins.me().status_code == 401
    assert entra_logins.me().status_code == 401
    assert (
        entra_logins.accounts.me(entra_logins.accounts.manager_pair).status_code == 200
    )


def test_current_roles_and_disabled_state_apply_to_entra(linked_logins) -> None:
    token = linked_logins.token()
    assert linked_logins.me(token).json()["roles"] == ["tender_manager"]
    linked_logins.accounts.update(linked_logins.accounts.manager_id, roles=["approver"])
    assert linked_logins.me(token).json()["roles"] == ["approver"]
    linked_logins.accounts.update(linked_logins.accounts.manager_id, active=False)
    assert linked_logins.me(token).status_code == 401


def test_unlinking_revokes_entra_but_preserves_local_login(linked_logins) -> None:
    token = linked_logins.token()
    assert linked_logins.me(token).status_code == 200
    assert linked_logins.unlink(linked_logins.identity["id"]).status_code == 204
    assert linked_logins.me(token).status_code == 401
    assert (
        linked_logins.accounts.me(linked_logins.accounts.manager_pair).status_code
        == 200
    )


def test_link_uniqueness_and_target_ownership_are_enforced(linked_logins) -> None:
    other = linked_logins.accounts.provision("other-external-user")["user"]["id"]
    assert linked_logins.link_response(user_id=other).status_code == 409
    assert (
        linked_logins.unlink(linked_logins.identity["id"], user_id=other).status_code
        == 404
    )
    assert linked_logins.me().status_code == 200


def test_only_current_administrators_can_manage_links(linked_logins) -> None:
    manager_headers = linked_logins.accounts.headers(
        linked_logins.accounts.manager_pair
    )
    assert (
        linked_logins.link_response(
            object_id=str(uuid4()), headers=manager_headers
        ).status_code
        == 403
    )
    assert (
        linked_logins.unlink(
            linked_logins.identity["id"], headers=manager_headers
        ).status_code
        == 403
    )
    linked_logins.accounts.update(linked_logins.accounts.admin_id, roles=[])
    assert linked_logins.unlink(linked_logins.identity["id"]).status_code == 403


def test_linking_rejects_other_tenants_and_invalid_object_ids(entra_logins) -> None:
    assert entra_logins.link_response(tenant_id=str(uuid4())).status_code == 422
    assert entra_logins.link_response(object_id="not-a-guid").status_code == 422


def test_link_and_unlink_audits_record_safe_provenance(linked_logins) -> None:
    token = linked_logins.token()
    linked_logins.me(token)
    linked_logins.unlink(linked_logins.identity["id"])
    with linked_logins.accounts.probe.session() as session:
        events = session.scalars(
            select(AuditEvent).where(
                AuditEvent.action.in_(["user.entra_linked", "user.entra_unlinked"])
            )
        ).all()
        assert {event.action for event in events} == {
            "user.entra_linked",
            "user.entra_unlinked",
        }
        for event in events:
            assert str(event.actor_id) == linked_logins.accounts.admin_id
            assert event.details["tenant_id"] == TENANT_ID
            assert event.details["object_id"] == OBJECT_ID
            assert event.details["external_identity_id"] == linked_logins.identity["id"]
            assert token not in json.dumps(event.details)


def test_signature_and_key_id_must_match_trusted_keys(linked_logins) -> None:
    assert (
        linked_logins.me(
            linked_logins.token(key_number=1, headers={"kid": "key-0"})
        ).status_code
        == 401
    )
    assert (
        linked_logins.me(
            linked_logins.token(headers={"kid": "unknown-key"})
        ).status_code
        == 401
    )


def test_token_metadata_cannot_select_an_arbitrary_key_endpoint(linked_logins) -> None:
    assert (
        linked_logins.me(
            linked_logins.token(headers={"jku": "https://untrusted.invalid/keys"})
        ).status_code
        == 200
    )
    assert linked_logins.fetches == [JWKS_URI]
    assert (
        linked_logins.me(
            linked_logins.token({"iss": "https://untrusted.invalid"})
        ).status_code
        == 401
    )
    assert linked_logins.fetches == [JWKS_URI]


def test_jwks_cache_bounds_unknown_key_refreshes(linked_logins) -> None:
    assert linked_logins.me().status_code == 200
    for i in range(4):
        assert (
            linked_logins.me(
                linked_logins.token(headers={"kid": f"unknown-{i}"})
            ).status_code
            == 401
        )
    assert linked_logins.fetches == [JWKS_URI]


def test_rotated_keys_replace_cached_keys_after_refresh_cooldown(
    linked_logins, monkeypatch
) -> None:
    first_token = linked_logins.token()
    assert linked_logins.me(first_token).status_code == 200
    linked_logins.key_number = 1
    second_token = linked_logins.token()
    assert linked_logins.me(second_token).status_code == 401
    clock = SimpleNamespace(monotonic=lambda: time.monotonic() + 31)
    monkeypatch.setattr("jwt.jwks_client.time", clock)
    assert linked_logins.me(second_token).status_code == 200
    assert linked_logins.me(first_token).status_code == 401
    assert linked_logins.fetches == [JWKS_URI, JWKS_URI]


def test_jwks_outage_uses_only_unexpired_cached_keys(
    linked_logins, monkeypatch
) -> None:
    token = linked_logins.token()
    assert linked_logins.me(token).status_code == 200
    linked_logins.unavailable = True
    assert linked_logins.me(token).status_code == 200
    monkeypatch.setattr(
        "jwt.jwk_set_cache.time",
        SimpleNamespace(monotonic=lambda: time.monotonic() + 301),
    )
    assert linked_logins.me(token).status_code == 503


@pytest.mark.parametrize("warm_cache", [False, True])
def test_failed_jwks_fetches_are_backed_off_without_discarding_cached_keys(
    linked_logins, monkeypatch, warm_cache
) -> None:
    cached_token = linked_logins.token()
    if warm_cache:
        assert linked_logins.me(cached_token).status_code == 200
    now = [time.monotonic() + 31]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr("jwt.jwks_client.time", clock)
    monkeypatch.setattr("jwt.jwk_set_cache.time", clock)
    monkeypatch.setattr(
        "tenderflow.infrastructure.auth.entra.time", clock, raising=False
    )
    linked_logins.unavailable = True
    for i in range(4):
        unknown = linked_logins.token(headers={"kid": f"outage-unknown-{i}"})
        assert linked_logins.me(unknown).status_code == 503
        if warm_cache:
            assert linked_logins.me(cached_token).status_code == 200
    assert len(linked_logins.fetches) == (2 if warm_cache else 1)

    now[0] += 31
    linked_logins.unavailable = False
    linked_logins.key_number = 1
    assert linked_logins.me().status_code == 200
    assert len(linked_logins.fetches) == (3 if warm_cache else 2)


def test_signing_key_issuer_is_restricted_to_the_configured_tenant(
    linked_logins,
) -> None:
    linked_logins.jwk_changes = {"issuer": "https://untrusted.invalid/tenant/v2.0"}
    assert linked_logins.me().status_code == 401


def test_entra_claims_signed_with_the_local_key_do_not_cross_trust_boundaries(
    linked_logins,
) -> None:
    claims = jwt.decode(linked_logins.token(), options={"verify_signature": False})
    key = linked_logins.accounts.probe.settings.local_signing_key.get_secret_value()
    token = jwt.encode(claims, key, algorithm="HS256")
    assert linked_logins.me(token).status_code == 401
    assert linked_logins.fetches == []


def test_key_service_errors_are_safe_and_do_not_break_local_auth(linked_logins) -> None:
    linked_logins.unavailable = True
    token = linked_logins.token()
    response = linked_logins.me(token)
    assert response.status_code == 503
    assert token not in response.text
    assert "Synthetic" not in response.text
    assert (
        linked_logins.accounts.me(linked_logins.accounts.manager_pair).status_code
        == 200
    )


def test_entra_can_authenticate_without_a_local_signing_key(linked_logins) -> None:
    settings = linked_logins.client.app.state.settings.model_copy(
        update={"local_signing_key": None}
    )
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/health/ready").status_code == 200
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {linked_logins.token()}"},
        )
        assert response.status_code == 200
        assert (
            client.post(
                "/api/v1/auth/local/login",
                json={"login": "manager", "password": "synthetic passphrase"},
            ).status_code
            == 503
        )


def test_openapi_describes_tenant_specific_authorization_code_pkce(
    entra_logins,
) -> None:
    document = entra_logins.client.get("/openapi.json").json()
    flow = document["components"]["securitySchemes"]["EntraOAuth2"]["flows"][
        "authorizationCode"
    ]
    assert (
        flow["authorizationUrl"]
        == f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize"
    )
    assert (
        flow["tokenUrl"]
        == f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    )
    assert SCOPE in flow["scopes"]
    assert {"EntraOAuth2": [SCOPE]} in document["paths"]["/api/v1/auth/me"]["get"][
        "security"
    ]
    assert "usePkceWithAuthorizationCodeGrant" in entra_logins.client.get("/docs").text
