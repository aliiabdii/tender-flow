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
