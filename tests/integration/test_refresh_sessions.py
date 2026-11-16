from concurrent.futures import ThreadPoolExecutor


def test_refresh_rotates_and_replay_revokes_family(local_accounts) -> None:
    first = local_accounts.manager_pair
    second = local_accounts.refresh(first["refresh_token"])
    assert second["refresh_token"] != first["refresh_token"]
    assert local_accounts.refresh_response(first["refresh_token"]).status_code == 401
    assert local_accounts.refresh_response(second["refresh_token"]).status_code == 401
    assert local_accounts.me(first).status_code == 401
    assert local_accounts.me(second).status_code == 401


def test_logout_revokes_the_whole_local_session_family(local_accounts) -> None:
    first = local_accounts.manager_pair
    second = local_accounts.refresh(first["refresh_token"])
    response = local_accounts.client.post(
        "/api/v1/auth/local/logout",
        json={"refresh_token": second["refresh_token"]},
    )
    assert response.status_code == 204
    assert local_accounts.me(first).status_code == 401
    assert local_accounts.me(second).status_code == 401
    assert local_accounts.refresh_response(second["refresh_token"]).status_code == 401


def test_concurrent_refresh_cannot_issue_two_valid_families(local_accounts) -> None:
    token = local_accounts.manager_pair["refresh_token"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(local_accounts.refresh_response, [token, token]))
    assert sorted(response.status_code for response in responses) == [200, 401]
    issued = next(
        response.json() for response in responses if response.status_code == 200
    )
    assert local_accounts.me(issued).status_code == 401
    assert local_accounts.refresh_response(issued["refresh_token"]).status_code == 401


def test_expired_refresh_cannot_be_used(local_accounts) -> None:
    token = local_accounts.manager_pair["refresh_token"]
    local_accounts.expire_refresh(token)
    assert local_accounts.refresh_response(token).status_code == 401
