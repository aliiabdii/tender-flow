import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tenderflow.config import Settings
from tenderflow.main import create_app


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in os.environ:
        if name.startswith("TENDERFLOW_"):
            monkeypatch.delenv(name)


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings=settings)) as test_client:
        yield test_client
