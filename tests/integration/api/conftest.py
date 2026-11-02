from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from mic3_api.main import create_app
from tests.integration.factories import AUTH, StubTokenValidator


@pytest.fixture
def validator() -> StubTokenValidator:
    return StubTokenValidator()


@pytest.fixture
def client(migrated_engine, postgres_test_settings, validator) -> Iterator[TestClient]:
    """An authenticated client on the migrated test database."""
    with TestClient(create_app(settings=postgres_test_settings, token_validator=validator),
                    headers=AUTH) as client:
        yield client


@pytest.fixture
def offline_client(test_settings, validator) -> Iterator[TestClient]:
    """A client whose database is unreachable, for checks that must not need one."""
    with TestClient(create_app(settings=test_settings, token_validator=validator)) as client:
        yield client
