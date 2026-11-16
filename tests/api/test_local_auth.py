import json

import pytest
from fastapi.testclient import TestClient

from tenderflow.main import create_app


def test_local_auth_requires_configuration(client) -> None:
    response = client.post(
        "/api/v1/auth/local/login",
        json={"login": "manager", "password": "unconfigured-password"},
    )
    assert response.status_code == 503


def test_admin_provisions_and_activates_a_normalized_account(local_accounts) -> None:
    account = local_accounts.provision(" New.Manager ", roles=["tender_manager"])
    assert account["user"]["login"] == "new.manager"
    local_accounts.activate(account["activation"]["token"])
    pair = local_accounts.login("NEW.MANAGER")

    response = local_accounts.me(pair)
    assert response.status_code == 200
    assert response.json()["id"] == account["user"]["id"]
    assert response.json()["roles"] == ["tender_manager"]
    assert local_accounts.provision_response("new.manager").status_code == 409


def test_non_administrator_cannot_provision_accounts(local_accounts) -> None:
    response = local_accounts.client.post(
        "/api/v1/admin/users",
        headers=local_accounts.headers(local_accounts.manager_pair),
        json={"login": "intruder", "display_name": "Synthetic", "roles": ["admin"]},
    )
    assert response.status_code == 403


def test_there_is_no_public_signup(local_accounts) -> None:
    assert (
        local_accounts.client.post("/api/v1/auth/local/signup", json={}).status_code
        == 404
    )
    assert local_accounts.client.post("/api/v1/admin/users", json={}).status_code == 401


def test_login_failures_do_not_reveal_account_existence(local_accounts) -> None:
    known = local_accounts.try_login("manager", "incorrect-password")
    unknown = local_accounts.try_login("unknown", "incorrect-password")
    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json()


@pytest.mark.parametrize("login", ["manager", "unknown"])
def test_login_throttling_is_independent_of_account_existence(
    local_accounts, login
) -> None:
    for _ in range(5):
        assert local_accounts.try_login(login, "incorrect-password").status_code == 401
    response = local_accounts.try_login(login, "incorrect-password")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert "password" not in response.text.lower()


def test_current_roles_are_loaded_on_each_request(local_accounts) -> None:
    local_accounts.update(local_accounts.manager_id, roles=["approver"])
    response = local_accounts.me(local_accounts.manager_pair)
    assert response.status_code == 200
    assert response.json()["roles"] == ["approver"]


def test_disabling_a_user_revokes_access_and_refresh(local_accounts) -> None:
    pair = local_accounts.manager_pair
    local_accounts.update(local_accounts.manager_id, active=False)
    assert local_accounts.me(pair).status_code == 401
    assert local_accounts.refresh_response(pair["refresh_token"]).status_code == 401
    assert local_accounts.try_login("manager").status_code == 401


def test_activation_is_single_use(local_accounts) -> None:
    account = local_accounts.provision("activation")
    token = account["activation"]["token"]
    local_accounts.activate(token)
    assert local_accounts.activate_response(token).status_code == 401


def test_expired_activation_is_rejected(local_accounts) -> None:
    account = local_accounts.provision("expired")
    token = account["activation"]["token"]
    local_accounts.expire_activation(token)
    assert local_accounts.activate_response(token).status_code == 401


@pytest.mark.parametrize("password", ["too-short", "x" * 129])
def test_password_policy_errors_do_not_echo_passwords(local_accounts, password) -> None:
    token = local_accounts.provision("policy")["activation"]["token"]
    response = local_accounts.activate_response(token, password)
    assert response.status_code == 422
    assert password not in response.text


def test_password_reset_revokes_existing_credentials_and_sessions(
    local_accounts,
) -> None:
    old_pair = local_accounts.manager_pair
    response = local_accounts.client.post(
        f"/api/v1/admin/users/{local_accounts.manager_id}/password-resets",
        headers=local_accounts.admin_headers,
    )
    assert response.status_code == 201
    assert local_accounts.me(old_pair).status_code == 401
    assert local_accounts.refresh_response(old_pair["refresh_token"]).status_code == 401
    assert local_accounts.try_login("manager").status_code == 401
    local_accounts.activate(response.json()["token"], password="replacement passphrase")
    assert (
        local_accounts.try_login("manager", "replacement passphrase").status_code == 200
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"exp": 1},
        {"iss": "wrong-issuer"},
        {"aud": "wrong-audience"},
        {"token_use": "refresh"},
        {"ver": 999},
        {"sid": "not-a-session-id"},
    ],
)
def test_invalid_access_tokens_are_rejected(local_accounts, changes) -> None:
    forged = local_accounts.changed_access_token(changes)
    assert local_accounts.me({"access_token": forged}).status_code == 401


def test_secrets_are_not_persisted_as_plaintext(local_accounts) -> None:
    pair = local_accounts.manager_pair
    stored = local_accounts.persisted_data()
    assert local_accounts.password not in stored
    assert pair["access_token"] not in stored
    assert pair["refresh_token"] not in stored
    assert local_accounts.bootstrap_token not in stored


def test_validation_errors_do_not_echo_tokens(local_accounts) -> None:
    token = local_accounts.manager_pair["refresh_token"]
    response = local_accounts.client.post(
        "/api/v1/auth/local/refresh",
        json={"refresh_token": token, "unexpected": token},
    )
    assert response.status_code == 422
    assert token not in response.text


def test_other_jwt_algorithms_are_not_accepted(local_accounts) -> None:
    token = local_accounts.changed_access_token({}, algorithm="HS384")
    assert local_accounts.me({"access_token": token}).status_code == 401


@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("login", {"login": "manager", "password": "synthetic passphrase\ud800"}),
        ("refresh", {"refresh_token": "synthetic-token\ud800"}),
    ],
)
def test_malformed_unicode_secrets_return_safe_validation_errors(
    local_accounts, route, body
) -> None:
    with TestClient(
        create_app(settings=local_accounts.probe.settings),
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            f"/api/v1/auth/local/{route}",
            content=json.dumps(body).encode("ascii"),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 422
    assert "synthetic" not in response.text
