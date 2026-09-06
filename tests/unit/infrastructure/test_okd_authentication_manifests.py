"""Contract tests for the EOSC/OKD authentication deployment manifests."""

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[3]
OKD = ROOT / "deploy" / "okd"
KEYCLOAK = OKD / "keycloak"
DIGEST_IMAGE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
PENDING_RELEASE_IMAGE = (
    "ghcr.io/transience-mic3/mic3-api:0.1.4"
    "@sha256:0000000000000000000000000000000000000000000000000000000000000000"
)
CONFIG_CLI_IMAGE = (
    "quay.io/adorsys/keycloak-config-cli:6.5.1-26@sha256:"
    "1b22dfaa9ae0c71f74b0342f9221a6510f272da5def683dbba26a98e6b1b1411"
)


def load_yaml_documents(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as manifest:
        return [document for document in yaml.safe_load_all(manifest) if document]


def find_resource(
    documents: list[dict[str, Any]], kind: str, name: str
) -> dict[str, Any]:
    return next(
        document
        for document in documents
        if document["kind"] == kind and document["metadata"]["name"] == name
    )


def container(resource: dict[str, Any]) -> dict[str, Any]:
    return resource["spec"]["template"]["spec"]["containers"][0]


def environment_by_name(resource: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["name"]: entry for entry in container(resource).get("env", [])}


def test_eosc_realm_is_closed_and_contains_no_seeded_authority() -> None:
    with (KEYCLOAK / "mic3-realm.json").open(encoding="utf-8") as realm_file:
        realm = json.load(realm_file)

    assert realm["realm"] == "mic3"
    assert realm["registrationAllowed"] is False
    assert realm["resetPasswordAllowed"] is False
    assert realm["verifyEmail"] is False
    assert {"users", "roles", "groups"}.isdisjoint(realm)


def test_eosc_realm_clients_have_the_required_oidc_contract() -> None:
    with (KEYCLOAK / "mic3-realm.json").open(encoding="utf-8") as realm_file:
        realm = json.load(realm_file)
    clients = {client["clientId"]: client for client in realm["clients"]}

    assert set(clients) == {"mic3-api", "mic3-postman"}
    assert clients["mic3-api"]["bearerOnly"] is True

    postman = clients["mic3-postman"]
    assert postman["publicClient"] is True
    assert postman["standardFlowEnabled"] is True
    assert postman["directAccessGrantsEnabled"] is False
    assert postman["attributes"]["pkce.code.challenge.method"] == "S256"
    assert postman["redirectUris"] == [
        "https://oauth.pstmn.io/v1/browser-callback"
    ]
    assert postman["defaultClientScopes"] == [
        "basic",
        "profile",
        "email",
        "web-origins",
    ]
    assert postman["protocolMappers"] == [
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


def test_keycloak_prerequisites_are_internal_and_isolated() -> None:
    prerequisites = load_yaml_documents(KEYCLOAK / "prerequisites.yaml")
    pvc = find_resource(
        prerequisites, "PersistentVolumeClaim", "mic3-keycloak-postgres-data"
    )
    postgres_service = find_resource(
        prerequisites, "Service", "mic3-keycloak-postgres"
    )
    keycloak_service = find_resource(prerequisites, "Service", "mic3-keycloak")
    route = find_resource(prerequisites, "Route", "mic3-keycloak")
    postgres = find_resource(
        prerequisites, "StatefulSet", "mic3-keycloak-postgres"
    )

    assert pvc["spec"]["accessModes"] == ["ReadWriteOncePod"]
    assert pvc["spec"]["storageClassName"] == "standard"
    assert pvc["spec"]["resources"]["requests"]["storage"] == "10Gi"
    assert postgres_service["spec"]["type"] == "ClusterIP"
    assert keycloak_service["spec"]["type"] == "ClusterIP"
    assert [port["port"] for port in keycloak_service["spec"]["ports"]] == [8080]
    assert route["spec"]["tls"] == {
        "termination": "edge",
        "insecureEdgeTerminationPolicy": "Redirect",
    }
    assert "host" not in route["spec"]
    assert DIGEST_IMAGE.fullmatch(container(postgres)["image"])

    postgres_env = environment_by_name(postgres)
    credential_secrets = {
        entry["valueFrom"]["secretKeyRef"]["name"]
        for entry in postgres_env.values()
        if "valueFrom" in entry
    }
    assert credential_secrets == {"mic3-keycloak-postgres-credentials"}
    assert "mic3-postgres" not in postgres_service["metadata"]["name"]


def test_keycloak_uses_production_settings_and_restricted_runtime() -> None:
    deployment = find_resource(
        load_yaml_documents(KEYCLOAK / "application.yaml"),
        "Deployment",
        "mic3-keycloak",
    )
    pod = deployment["spec"]["template"]["spec"]
    keycloak = container(deployment)
    env = environment_by_name(deployment)

    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert keycloak["args"] == ["start"]
    assert DIGEST_IMAGE.fullmatch(keycloak["image"])
    assert keycloak["resources"] == {
        "requests": {"cpu": "500m", "memory": "1Gi"},
        "limits": {"cpu": "1", "memory": "2Gi"},
    }
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert keycloak["securityContext"]["allowPrivilegeEscalation"] is False
    assert keycloak["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert env["KC_HTTP_ENABLED"]["value"] == "true"
    assert env["KC_PROXY_HEADERS"]["value"] == "xforwarded"
    assert env["KC_HOSTNAME_BACKCHANNEL_DYNAMIC"]["value"] == "true"
    assert env["KC_HOSTNAME"]["valueFrom"]["configMapKeyRef"]["name"] == (
        "mic3-keycloak-runtime"
    )
    assert env["KCRAW_DB_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == (
        "mic3-keycloak-postgres-credentials"
    )
    assert env["KC_BOOTSTRAP_ADMIN_PASSWORD"]["valueFrom"]["secretKeyRef"][
        "name"
    ] == "mic3-keycloak-bootstrap-admin"
    assert {probe["httpGet"]["port"] for probe in (
        keycloak["startupProbe"],
        keycloak["readinessProbe"],
        keycloak["livenessProbe"],
    )} == {"management"}
    assert "volumes" not in pod
    assert "volumeMounts" not in keycloak


def test_keycloak_config_map_has_a_stable_name() -> None:
    kustomization = yaml.safe_load(
        (KEYCLOAK / "kustomization.yaml").read_text(encoding="utf-8")
    )

    assert kustomization["configMapGenerator"] == [
        {"name": "mic3-keycloak-realm", "files": ["mic3-realm.json"]}
    ]
    assert kustomization["generatorOptions"]["disableNameSuffixHash"] is True
    assert kustomization["resources"] == [
        "prerequisites.yaml",
        "application.yaml",
    ]


def test_keycloak_configuration_job_is_explicit_and_restricted() -> None:
    job = load_yaml_documents(KEYCLOAK / "configure.yaml")[0]
    config = container(job)
    pod = job["spec"]["template"]["spec"]
    env = environment_by_name(job)

    assert job["kind"] == "Job"
    assert "name" not in job["metadata"]
    assert job["metadata"]["generateName"] == "mic3-keycloak-configure-"
    assert job["spec"]["ttlSecondsAfterFinished"] == 86400
    assert job["spec"]["activeDeadlineSeconds"] == 300
    assert config["image"] == CONFIG_CLI_IMAGE
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert config["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert env["KEYCLOAK_URL"]["value"] == "http://mic3-keycloak:8080"
    assert env["KEYCLOAK_USER"]["valueFrom"]["secretKeyRef"]["name"] == (
        "mic3-keycloak-bootstrap-admin"
    )
    assert env["KEYCLOAK_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == (
        "mic3-keycloak-bootstrap-admin"
    )
    assert env["IMPORT_FILES_LOCATIONS"]["value"] == "file:/config/*"
    assert env["IMPORT_VALIDATE"]["value"] == "true"
    assert env["IMPORT_REMOTESTATE_ENABLED"]["value"] == "true"
    assert env["IMPORT_VARSUBSTITUTION_ENABLED"]["value"] == "true"
    assert env["LOGGING_LEVEL_ROOT"]["value"] == "INFO"
    assert all("TRACE" not in str(entry).upper() for entry in env.values())
    secret_names = {
        entry["valueFrom"]["secretKeyRef"]["name"]
        for entry in env.values()
        if "valueFrom" in entry
    }
    assert secret_names == {"mic3-keycloak-bootstrap-admin"}
    assert {volume["name"] for volume in pod["volumes"]} == {
        "realm-config",
        "temporary-files",
    }
    realm_volume = next(
        volume for volume in pod["volumes"] if volume["name"] == "realm-config"
    )
    assert realm_volume["configMap"]["name"] == "mic3-keycloak-realm"


def test_api_receives_oidc_settings_from_the_dedicated_secret() -> None:
    deployment = find_resource(
        load_yaml_documents(OKD / "application.yaml"), "Deployment", "mic3-api"
    )
    env = environment_by_name(deployment)

    for variable, key in (
        ("OIDC_ISSUER_URL", "issuer-url"),
        ("OIDC_AUDIENCE", "audience"),
    ):
        assert env[variable]["valueFrom"]["secretKeyRef"] == {
            "name": "mic3-oidc-config",
            "key": key,
        }


def test_migration_job_runs_only_alembic_with_mic3_database_settings() -> None:
    job = find_resource(
        load_yaml_documents(OKD / "migration.yaml"),
        "Job",
        "mic3-api-migrate-0-1-4",
    )
    migration = container(job)
    env = environment_by_name(job)

    assert migration["command"] == [
        "python",
        "-m",
        "alembic",
        "upgrade",
        "head",
    ]
    assert set(env) == {"DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"}
    assert env["DB_HOST"]["value"] == "mic3-postgres"
    assert {
        env[name]["valueFrom"]["secretKeyRef"]["name"]
        for name in ("DB_NAME", "DB_USER", "DB_PASSWORD")
    } == {"mic3-postgres-credentials"}


def test_release_image_promotion_is_explicit_and_updates_both_workloads() -> None:
    api = find_resource(
        load_yaml_documents(OKD / "application.yaml"), "Deployment", "mic3-api"
    )
    migration = find_resource(
        load_yaml_documents(OKD / "migration.yaml"),
        "Job",
        "mic3-api-migrate-0-1-4",
    )
    api_image = container(api)["image"]
    migration_image = container(migration)["image"]

    if migration_image == PENDING_RELEASE_IMAGE:
        # The source/release commit deliberately retains the deployed API and
        # cannot invent the v0.1.4 digest. Promotion must replace both values.
        assert DIGEST_IMAGE.fullmatch(api_image)
        assert ":0.1.3@sha256:" in api_image
    else:
        assert api_image == migration_image
        assert api_image.startswith(
            "ghcr.io/transience-mic3/mic3-api:0.1.4@sha256:"
        )
        assert DIGEST_IMAGE.fullmatch(api_image)


def test_manifests_do_not_commit_cluster_specific_or_secret_resources() -> None:
    paths = [
        OKD / "application.yaml",
        OKD / "postgres.yaml",
        OKD / "migration.yaml",
        KEYCLOAK / "prerequisites.yaml",
        KEYCLOAK / "application.yaml",
        KEYCLOAK / "configure.yaml",
    ]

    for path in paths:
        for resource in load_yaml_documents(path):
            assert resource["kind"] != "Secret"
            assert "namespace" not in resource["metadata"]
            if resource["kind"] == "Route":
                assert "host" not in resource["spec"]


def test_release_version_and_publish_workflow_are_consistent() -> None:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        version = tomllib.load(project_file)["project"]["version"]
    workflow = (ROOT / ".github" / "workflows" / "publish-image.yml").read_text(
        encoding="utf-8"
    )

    assert version == "0.1.4"
    assert 'release_tag = os.environ["RELEASE_TAG"]' in workflow
    assert 'expected_tag = f"v{package_version}"' in workflow
    assert "python -m pytest tests/unit tests/integration" in workflow
