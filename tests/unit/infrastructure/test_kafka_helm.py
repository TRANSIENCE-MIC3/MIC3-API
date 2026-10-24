"""Inspect actual chart output, including certificate and storage boundaries."""
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


CHART = Path(__file__).resolve().parents[3] / "deploy/helm/kafka"
pytestmark = pytest.mark.skipif(not shutil.which("helm"), reason="Helm required")


def render(*args):
    result = subprocess.run(["helm", "template", "test", str(CHART), "--namespace", "sandbox", *args],
                            capture_output=True, text=True, check=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def enabled(*args):
    return render("--set", "broker.enabled=true", "--set", "broker.clusterId=MkMza2Fma2Fsb2NhbDAwMQ", *args)


def test_default_stage_only_issues_certificates():
    docs = render()
    assert {doc["kind"] for doc in docs} == {"Issuer", "Certificate"}
    leaves = [doc for doc in docs if doc["kind"] == "Certificate" and not doc["spec"].get("isCA")]
    assert len(leaves) == 4
    for certificate in leaves:
        spec = certificate["spec"]
        assert spec["secretName"].endswith("-tls")
        assert spec["privateKey"]["encoding"] == "PKCS8"
        assert spec["privateKey"]["rotationPolicy"] == "Always"
        assert "client auth" in spec["usages"]
        if spec["commonName"].endswith("broker"):
            assert "server auth" in spec["usages"]
            assert spec["dnsNames"] == ["mic3-kafka.sandbox.svc.cluster.local", "mic3-kafka-0.mic3-kafka-headless.sandbox.svc.cluster.local"]
        else:
            assert "server auth" not in spec["usages"]


def test_broker_uses_retained_storage_separate_trust_and_no_ca_signing_key():
    docs = enabled()
    assert not {"Route", "Ingress", "ClusterRole", "ClusterRoleBinding"} & {doc["kind"] for doc in docs}
    pvc = next(doc for doc in docs if doc["kind"] == "PersistentVolumeClaim")
    assert pvc["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    assert pvc["spec"]["resources"]["requests"]["storage"] == "10Gi"
    for doc in docs:
        if doc["kind"] not in {"StatefulSet", "Job"}:
            continue
        pod = doc["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert "runAsUser" not in pod["securityContext"]
        assert pod["securityContext"]["runAsNonRoot"] is True
        container = pod["containers"][0]
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert "@sha256:" in container["image"]
        secrets = [volume["secret"]["secretName"] for volume in pod["volumes"] if "secret" in volume]
        assert secrets == ["mic3-kafka-broker-tls" if doc["kind"] == "StatefulSet" else "mic3-kafka-operator-tls"]
        trust = next(volume for volume in pod["volumes"] if volume["name"] == "trust")
        assert trust["configMap"]["name"] == "mic3-kafka-trust"


def test_both_listeners_require_tls_and_only_broker_operator_are_superusers():
    docs = enabled()
    data = next(doc["data"] for doc in docs if doc["kind"] == "ConfigMap")
    config = dict(line.split("=", 1) for line in data["server.properties"].splitlines() if "=" in line)
    assert config["listener.security.protocol.map"] == "CLIENT:SSL,CONTROLLER:SSL"
    assert config["ssl.client.auth"] == "required"
    assert config["ssl.endpoint.identification.algorithm"] == "https"
    assert config["allow.everyone.if.no.acl.found"] == "false"
    assert config["auto.create.topics.enable"] == "false"
    assert config["super.users"] == "User:CN=mic3-kafka-broker;User:CN=mic3-kafka-operator"


@pytest.mark.parametrize("override", ["broker.enabled=true", "image=apache/kafka:latest", "name=bad.name", "topics.retentionBytes=-1", "broker.clusterId=invalid", "podIdentity.runAsUser=0"])
def test_invalid_or_incomplete_configuration_is_rejected(override):
    with pytest.raises(subprocess.CalledProcessError):
        render("--set", override)


def test_custom_name_and_namespace_propagate_to_dns_and_identities():
    docs = enabled("--set", "name=other-kafka", "--set", "clusterDomain=example.internal")
    data = next(doc["data"] for doc in docs if doc["kind"] == "ConfigMap")
    assert "other-kafka.sandbox.svc.example.internal:9092" in data["server.properties"]
    assert "User:CN=other-kafka-publisher" not in data["server.properties"]
    assert "User:CN=other-kafka-operator" in data["server.properties"]


def test_dns_egress_and_client_ingress_are_explicit():
    docs = enabled()
    policies = {doc["metadata"]["name"]:doc["spec"] for doc in docs if doc["kind"] == "NetworkPolicy"}
    dns = policies["mic3-kafka-egress"]["egress"][0]["ports"]
    assert {(port["protocol"], port["port"]) for port in dns} == {("UDP",53),("TCP",53),("UDP",5353),("TCP",5353)}
    ingress = policies["mic3-kafka-ingress"]["ingress"][0]
    assert ingress["from"] == [{"podSelector":{"matchLabels":{"mic3.io/kafka-client":"mic3-kafka"}}}]
    assert ingress["ports"] == [{"protocol":"TCP","port":9092}]


def test_request_and_terminal_topics_cannot_be_combined():
    with pytest.raises(subprocess.CalledProcessError):
        enabled("--set", "topics.events=mic3.run.requests.v1")
