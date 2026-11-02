import pytest

from mic3_api.core.config import Settings


class IdleNotifications:
    """Stands in for the Postgres LISTEN thread, which needs a reachable database."""

    def __init__(self, *_):
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


@pytest.fixture(autouse=True)
def idle_notifications(request, monkeypatch):
    """Apps built in tests skip the notification listener unless a test opts in.

    Starting it against an unreachable database costs a connect timeout per app.
    Mark a module or test with @pytest.mark.live_notifications to keep it.
    """
    if request.node.get_closest_marker("live_notifications") is None:
        monkeypatch.setattr("mic3_api.main.RunNotifications", IdleNotifications)


@pytest.fixture
def test_settings() -> Settings:
    """Return complete settings without reading developer or deployment secrets."""
    return Settings(
        app_env="test",
        app_name="mic3-api",
        db_host="localhost",
        db_port=5433,
        db_name="mic3",
        db_user="mic3_api",
        db_password="test-only-password",
        oidc_issuer_url="https://issuer.test/realms/mic3",
        oidc_audience="mic3-api",
    )
