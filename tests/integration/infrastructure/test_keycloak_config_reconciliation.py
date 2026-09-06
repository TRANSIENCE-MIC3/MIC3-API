"""Compatibility tests for declarative Keycloak realm reconciliation."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from testcontainers.core.wait_strategies import HttpWaitStrategy, LogMessageWaitStrategy


ROOT = Path(__file__).resolve().parents[3]
LOCAL_REALM = ROOT / "deploy" / "local" / "keycloak" / "config"
EOSC_REALM = ROOT / "deploy" / "okd" / "keycloak"
POSTGRES_IMAGE = (
    "postgres:18@sha256:"
    "4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280"
)
KEYCLOAK_IMAGE = (
    "quay.io/keycloak/keycloak:26.7.3@sha256:"
    "ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54"
)
CONFIG_CLI_IMAGE = (
    "quay.io/adorsys/keycloak-config-cli:6.5.1-26@sha256:"
    "1b22dfaa9ae0c71f74b0342f9221a6510f272da5def683dbba26a98e6b1b1411"
)
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "test-only-keycloak-admin-password"
DATABASE_PASSWORD = "test-only-keycloak-database-password"


def _container_output(container: DockerContainer) -> str:
    stdout, stderr = container.get_logs()
    return stdout.decode(errors="replace") + stderr.decode(errors="replace")


def _run_config_cli(network: Network, config_directory: Path) -> tuple[int, str]:
    config = (
        DockerContainer(CONFIG_CLI_IMAGE, network=network, read_only=True)
        .with_env("KEYCLOAK_URL", "http://keycloak:8080")
        .with_env("KEYCLOAK_USER", ADMIN_USERNAME)
        .with_env("KEYCLOAK_PASSWORD", ADMIN_PASSWORD)
        .with_env("KEYCLOAK_LOGINREALM", "master")
        .with_env("KEYCLOAK_AVAILABILITYCHECK_ENABLED", "true")
        .with_env("KEYCLOAK_AVAILABILITYCHECK_TIMEOUT", "120s")
        .with_env("IMPORT_FILES_LOCATIONS", "file:/config/*")
        .with_env("IMPORT_VALIDATE", "true")
        .with_env("IMPORT_REMOTESTATE_ENABLED", "true")
        .with_env("IMPORT_VARSUBSTITUTION_ENABLED", "true")
        .with_env("IMPORT_BEHAVIORS_CHECKSUM_CHANGED", "continue")
        .with_env("LOGGING_LEVEL_ROOT", "INFO")
        .with_volume_mapping(config_directory.resolve(), "/config", "ro")
    )
    config.tmpfs["/tmp"] = "rw,nosuid,nodev,size=64m"
    try:
        config.start()
        result = config.get_wrapped_container().wait(timeout=180)
        return int(result["StatusCode"]), _container_output(config)
    finally:
        config.stop()


@contextmanager
def _keycloak_stack() -> Iterator[tuple[Network, str]]:
    with Network() as network:
        postgres = (
            DockerContainer(
                POSTGRES_IMAGE,
                network=network,
                network_aliases=["postgres"],
            )
            .with_env("POSTGRES_DB", "keycloak")
            .with_env("POSTGRES_USER", "keycloak")
            .with_env("POSTGRES_PASSWORD", DATABASE_PASSWORD)
            .waiting_for(
                LogMessageWaitStrategy(
                    "database system is ready to accept connections", times=2
                )
                .with_startup_timeout(120)
            )
        )
        keycloak = (
            DockerContainer(
                KEYCLOAK_IMAGE,
                command=["start-dev"],
                network=network,
                network_aliases=["keycloak"],
                mem_limit="1g",
            )
            .with_env("KC_DB", "postgres")
            .with_env("KC_DB_URL", "jdbc:postgresql://postgres:5432/keycloak")
            .with_env("KC_DB_USERNAME", "keycloak")
            .with_env("KC_DB_PASSWORD", DATABASE_PASSWORD)
            .with_env("KC_BOOTSTRAP_ADMIN_USERNAME", ADMIN_USERNAME)
            .with_env("KC_BOOTSTRAP_ADMIN_PASSWORD", ADMIN_PASSWORD)
            .with_env("KC_HOSTNAME", "http://localhost:8080")
            .with_env("KC_HOSTNAME_BACKCHANNEL_DYNAMIC", "true")
            .with_exposed_ports(8080)
            .waiting_for(
                HttpWaitStrategy(8080, "/realms/master").with_startup_timeout(180)
            )
        )

        try:
            postgres.start()
            keycloak.start()
            host = keycloak.get_container_host_ip()
            port = keycloak.get_exposed_port(8080)
            yield network, f"http://{host}:{port}"
        finally:
            keycloak.stop()
            postgres.stop()


def _admin_token(client: httpx.Client, base_url: str) -> str:
    response = client.post(
        f"{base_url}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
        },
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def _admin_headers(client: httpx.Client, base_url: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_admin_token(client, base_url)}"}


def _realm(client: httpx.Client, base_url: str) -> dict[str, Any]:
    response = client.get(
        f"{base_url}/admin/realms/mic3",
        headers=_admin_headers(client, base_url),
    )
    response.raise_for_status()
    return response.json()


def _clients(client: httpx.Client, base_url: str) -> dict[str, dict[str, Any]]:
    response = client.get(
        f"{base_url}/admin/realms/mic3/clients",
        headers=_admin_headers(client, base_url),
    )
    response.raise_for_status()
    return {item["clientId"]: item for item in response.json()}


def _create_user(client: httpx.Client, base_url: str) -> None:
    response = client.post(
        f"{base_url}/admin/realms/mic3/users",
        headers=_admin_headers(client, base_url),
        json={"username": "reconciliation-survivor", "enabled": True},
    )
    assert response.status_code == 201, response.text


def _usernames(client: httpx.Client, base_url: str) -> set[str]:
    response = client.get(
        f"{base_url}/admin/realms/mic3/users",
        headers=_admin_headers(client, base_url),
    )
    response.raise_for_status()
    return {user["username"] for user in response.json()}


def test_keycloak_26_reconciliation_is_idempotent_and_preserves_users(
    tmp_path: Path,
) -> None:
    with _keycloak_stack() as (network, base_url), httpx.Client(
        timeout=30
    ) as client:
        first_status, first_logs = _run_config_cli(network, LOCAL_REALM)
        assert first_status == 0, first_logs
        assert _realm(client, base_url)["registrationAllowed"] is True
        assert {"mic3-api", "mic3-local"}.issubset(_clients(client, base_url))

        second_status, second_logs = _run_config_cli(network, LOCAL_REALM)
        assert second_status == 0, second_logs

        _create_user(client, base_url)

        with (LOCAL_REALM / "mic3-realm.json").open(encoding="utf-8") as source:
            changed_realm = json.load(source)
        changed_client = next(
            item
            for item in changed_realm["clients"]
            if item["clientId"] == "mic3-local"
        )
        changed_client["redirectUris"].append("http://localhost:3000/callback")
        changed_directory = tmp_path / "changed"
        changed_directory.mkdir()
        (changed_directory / "mic3-realm.json").write_text(
            json.dumps(changed_realm), encoding="utf-8"
        )

        changed_status, changed_logs = _run_config_cli(network, changed_directory)
        assert changed_status == 0, changed_logs
        assert "reconciliation-survivor" in _usernames(client, base_url)
        assert "http://localhost:3000/callback" in _clients(client, base_url)[
            "mic3-local"
        ]["redirectUris"]

        restored_status, restored_logs = _run_config_cli(network, LOCAL_REALM)
        assert restored_status == 0, restored_logs
        assert "http://localhost:3000/callback" not in _clients(client, base_url)[
            "mic3-local"
        ]["redirectUris"]
        assert "reconciliation-survivor" in _usernames(client, base_url)

        delete_response = client.delete(
            f"{base_url}/admin/realms/mic3",
            headers=_admin_headers(client, base_url),
        )
        assert delete_response.status_code == 204, delete_response.text

        eosc_directory = tmp_path / "eosc"
        eosc_directory.mkdir()
        (eosc_directory / "mic3-realm.json").write_text(
            (EOSC_REALM / "mic3-realm.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        eosc_status, eosc_logs = _run_config_cli(network, eosc_directory)
        assert eosc_status == 0, eosc_logs
        assert _realm(client, base_url)["registrationAllowed"] is False
        assert {"mic3-api", "mic3-postman"}.issubset(_clients(client, base_url))
        assert _usernames(client, base_url) == set()

        malformed_directory = tmp_path / "malformed"
        malformed_directory.mkdir()
        (malformed_directory / "mic3-realm.json").write_text(
            '{"realm": "mic3",', encoding="utf-8"
        )
        malformed_status, _ = _run_config_cli(network, malformed_directory)
        assert malformed_status != 0
