import pytest
from pydantic import ValidationError

from tenderflow.config import Settings


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_upload_bytes", 0),
        ("max_upload_bytes", -1),
        ("max_documents_per_tender", 0),
        ("max_lines_per_tender", 0),
        ("default_page_size", 0),
        ("max_page_size", 0),
    ],
)
def test_invalid_limits_are_rejected(field: str, value: int) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(**{field: value})

    assert error.value.errors()[0]["loc"] == (field,)


def test_default_page_size_cannot_exceed_maximum() -> None:
    with pytest.raises(ValidationError):
        Settings(default_page_size=201, max_page_size=200)


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENDERFLOW_MAX_UPLOAD_BYTES", "1024")

    assert Settings().max_upload_bytes == 1024


def test_invalid_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENDERFLOW_MAX_UPLOAD_BYTES", "unlimited")

    with pytest.raises(ValidationError):
        Settings()


def test_database_credentials_are_not_printable() -> None:
    url = "postgresql+psycopg2://test:synthetic-password@localhost/test"

    assert "synthetic-password" not in repr(Settings(database_url=url))


def test_unsupported_database_url_error_does_not_expose_credentials() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(database_url="mysql://test:synthetic-password@localhost/test")

    assert "synthetic-password" not in str(error.value)


def test_worker_timeout_must_allow_for_heartbeat_updates() -> None:
    with pytest.raises(ValidationError):
        Settings(worker_heartbeat_seconds=10, worker_stalled_seconds=20)


@pytest.mark.parametrize("field", ["local_token_issuer", "local_token_audience"])
def test_local_token_trust_identifiers_cannot_be_blank(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: ""})


def test_short_local_signing_key_is_rejected_without_disclosure() -> None:
    key = "synthetic-short-key"
    with pytest.raises(ValidationError) as error:
        Settings(local_signing_key=key)
    assert key not in str(error.value)


@pytest.mark.parametrize(
    "values",
    [
        {"entra_tenant_id": "11111111-1111-4111-8111-111111111111"},
        {"entra_required_scope": "api://synthetic/access_as_user"},
        {
            "entra_tenant_id": "not-a-tenant",
            "entra_audience": "33333333-3333-4333-8333-333333333333",
            "entra_required_scope": "api://synthetic/access_as_user",
        },
        {
            "entra_tenant_id": "11111111-1111-4111-8111-111111111111",
            "entra_audience": "33333333-3333-4333-8333-333333333333",
            "entra_required_scope": "",
        },
        {
            "entra_tenant_id": "11111111-1111-4111-8111-111111111111",
            "entra_audience": "33333333-3333-4333-8333-333333333333",
            "entra_required_scope": "api://synthetic/access_as_user other_scope",
        },
    ],
)
def test_entra_configuration_must_be_complete_and_valid(values) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)
