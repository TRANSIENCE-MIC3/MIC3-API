import json
from pathlib import Path
from typing import Any


REALM_IMPORT = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "local"
    / "keycloak"
    / "import"
    / "mic3-realm.json"
)


def load_realm() -> dict[str, Any]:
    with REALM_IMPORT.open(encoding="utf-8") as realm_file:
        return json.load(realm_file)


def test_realm_import_has_stable_local_oidc_contract() -> None:
    realm = load_realm()
    clients = {client["clientId"]: client for client in realm["clients"]}

    assert realm["realm"] == "mic3"
    assert realm["enabled"] is True
    assert "roles" not in realm
    assert set(clients) == {"mic3-api", "mic3-local"}

    api_client = clients["mic3-api"]
    assert api_client["bearerOnly"] is True
    assert api_client["directAccessGrantsEnabled"] is False

    local_client = clients["mic3-local"]
    assert local_client["publicClient"] is True
    assert local_client["standardFlowEnabled"] is True
    assert local_client["directAccessGrantsEnabled"] is False
    assert local_client["attributes"]["pkce.code.challenge.method"] == "S256"
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


def test_realm_import_has_one_non_elevated_local_identity() -> None:
    realm = load_realm()

    assert realm["users"] == [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "username": "${KEYCLOAK_DEV_USERNAME}",
            "email": "${KEYCLOAK_DEV_EMAIL}",
            "emailVerified": True,
            "enabled": True,
            "credentials": [
                {
                    "type": "password",
                    "value": "${KEYCLOAK_DEV_PASSWORD}",
                    "temporary": False,
                }
            ],
        }
    ]
