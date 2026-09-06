import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
REALM_CONFIG = (
    ROOT
    / "deploy"
    / "local"
    / "keycloak"
    / "config"
    / "mic3-realm.json"
)
CONFIG_CLI_IMAGE = (
    "quay.io/adorsys/keycloak-config-cli:6.5.1-26@sha256:"
    "1b22dfaa9ae0c71f74b0342f9221a6510f272da5def683dbba26a98e6b1b1411"
)


def load_realm() -> dict[str, Any]:
    with REALM_CONFIG.open(encoding="utf-8") as realm_file:
        return json.load(realm_file)


def test_local_realm_config_has_stable_oidc_contract() -> None:
    realm = load_realm()
    clients = {client["clientId"]: client for client in realm["clients"]}

    assert realm["realm"] == "mic3"
    assert realm["enabled"] is True
    assert realm["registrationAllowed"] is True
    assert realm["verifyEmail"] is False
    assert {"users", "roles", "groups"}.isdisjoint(realm)
    assert set(clients) == {"mic3-api", "mic3-local"}

    api_client = clients["mic3-api"]
    assert api_client["bearerOnly"] is True
    assert api_client["directAccessGrantsEnabled"] is False

    local_client = clients["mic3-local"]
    assert local_client["publicClient"] is True
    assert local_client["standardFlowEnabled"] is True
    assert local_client["directAccessGrantsEnabled"] is False
    assert local_client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert local_client["defaultClientScopes"] == [
        "basic",
        "profile",
        "email",
        "web-origins",
    ]
    assert local_client["redirectUris"] == [
        "http://localhost:8000/docs/oauth2-redirect",
        "https://oauth.pstmn.io/v1/browser-callback",
    ]
    assert local_client["protocolMappers"] == [
        {
            "name": "mic3-api-audience",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper",
            "consentRequired": False,
            "config": {
                "included.client.audience": "mic3-api",
                "id.token.claim": "false",
                "access.token.claim": "true",
                "lightweight.claim": "false",
                "introspection.token.claim": "true",
            },
        }
    ]


def test_compose_runs_reconciliation_as_an_explicit_one_shot_tool() -> None:
    with (ROOT / "compose.yaml").open(encoding="utf-8") as compose_file:
        services = yaml.safe_load(compose_file)["services"]

    keycloak = services["keycloak"]
    config = services["keycloak-config"]

    assert keycloak["command"] == ["start-dev"]
    assert "volumes" not in keycloak
    assert keycloak["environment"]["KC_HOSTNAME_BACKCHANNEL_DYNAMIC"] == "true"
    assert config["image"] == CONFIG_CLI_IMAGE
    assert config["profiles"] == ["tools"]
    assert config["depends_on"]["keycloak"]["condition"] == "service_healthy"
    assert config["volumes"] == [
        "./deploy/local/keycloak/config:/config:ro"
    ]

    environment = config["environment"]
    assert environment["KEYCLOAK_URL"] == "http://keycloak:8080"
    assert environment["KEYCLOAK_LOGINREALM"] == "master"
    assert environment["IMPORT_FILES_LOCATIONS"] == "file:/config/*"
    assert environment["IMPORT_VALIDATE"] == "true"
    assert environment["IMPORT_REMOTESTATE_ENABLED"] == "true"
    assert environment["IMPORT_VARSUBSTITUTION_ENABLED"] == "true"
    assert environment["LOGGING_LEVEL_ROOT"] == "INFO"
    assert all("TRACE" not in str(value).upper() for value in environment.values())


def test_local_environment_has_no_seeded_user_credentials() -> None:
    environment_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "KEYCLOAK_DEV_USERNAME" not in environment_example
    assert "KEYCLOAK_DEV_EMAIL" not in environment_example
    assert "KEYCLOAK_DEV_PASSWORD" not in environment_example
