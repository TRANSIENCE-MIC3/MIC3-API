from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from mic3_api.core.execution_config import ExecutionSettings
from mic3_api.core.publisher_config import PublisherSettings
from mic3_api.core.worker_config import WorkerSettings
from mic3_api.infrastructure import kafka_consumption, kafka_publication, kubernetes_jobs
from mic3_api.infrastructure.kafka_security import security_config


DB = dict(db_host="localhost", db_name="test", db_user="test", db_password="test", _env_file=None)


@pytest.fixture
def tls_files(tmp_path):
    result = {}
    for name in ("ca", "certificate", "key"):
        path = tmp_path / name
        path.write_text("test placeholder; transport constructors are mocked")
        result[f"kafka_ssl_{name}_file"] = path
    return result


@pytest.mark.parametrize("settings_type", [PublisherSettings, WorkerSettings])
def test_tls_requires_complete_identity_and_never_silently_uses_plaintext(settings_type, tls_files):
    assert security_config(settings_type(**DB)) == {"security.protocol": "PLAINTEXT"}
    for missing in tls_files:
        incomplete = {key: value for key, value in tls_files.items() if key != missing}
        with pytest.raises(ValidationError):
            settings_type(**DB, kafka_security_protocol="SSL", **incomplete)
    with pytest.raises(ValidationError):
        settings_type(**DB, **tls_files)
    with pytest.raises(ValidationError):
        settings_type(**DB, kafka_security_protocol="SASL_PLAINTEXT")
    with pytest.raises(ValidationError):
        settings_type(**DB, kafka_security_protocol="SSL", **(tls_files | {"kafka_ssl_key_file": "missing-key.pem"}))


def test_both_transports_use_certificate_and_hostname_verification(monkeypatch, tls_files):
    producer = Mock()
    consumer = Mock()
    consumer.return_value.list_topics.return_value = SimpleNamespace(topics={"test.requests": SimpleNamespace(error=None)})
    monkeypatch.setattr(kafka_publication, "Producer", producer)
    monkeypatch.setattr(kafka_consumption, "Consumer", consumer)
    publisher_settings = PublisherSettings(**DB, kafka_security_protocol="SSL", **tls_files)
    worker_settings = WorkerSettings(**DB, kafka_security_protocol="SSL", **tls_files)
    kafka_publication.KafkaEventPublisher("broker:9093", "test.requests", security=publisher_settings)
    kafka_consumption.KafkaRequestConsumer("broker:9093", "test.requests", "test.group", security=worker_settings)
    for factory in (producer, consumer):
        config = factory.call_args.args[0]
        assert config["security.protocol"] == "SSL"
        assert config["ssl.ca.location"] == str(tls_files["kafka_ssl_ca_file"])
        assert config["ssl.certificate.location"] == str(tls_files["kafka_ssl_certificate_file"])
        assert config["ssl.key.location"] == str(tls_files["kafka_ssl_key_file"])
        assert config["enable.ssl.certificate.verification"] is True
        assert config["ssl.endpoint.identification.algorithm"] == "https"
    assert consumer.call_args.args[0]["enable.auto.commit"] is False
    assert producer.call_args.args[0]["enable.idempotence"] is True


def test_execution_settings_require_both_local_auth_fields_or_neither():
    values = DB | dict(execution_namespace="test", artifact_endpoint="http://storage:9000",
                       artifact_bucket="test", artifact_access_key="test", artifact_secret_key="test")
    assert ExecutionSettings(**values).execution_kubeconfig is None
    local = ExecutionSettings(**values, execution_kubeconfig="local.yaml", execution_context="local")
    assert local.execution_context == "local"
    for partial in ({"execution_kubeconfig": "local.yaml"}, {"execution_context": "local"}):
        with pytest.raises(ValidationError):
            ExecutionSettings(**values, **partial)


@pytest.mark.parametrize("local", [False, True])
def test_kubernetes_auth_selects_exactly_one_source(monkeypatch, local):
    incluster, kubeconfig = Mock(), Mock()
    monkeypatch.setattr(kubernetes_jobs.config, "load_incluster_config", incluster)
    monkeypatch.setattr(kubernetes_jobs.config, "load_kube_config", kubeconfig)
    jobs = kubernetes_jobs.KubernetesJobs.connect(
        Path("local.yaml") if local else None, "local" if local else None, Mock(), "test")
    try:
        assert incluster.call_count == (0 if local else 1)
        assert kubeconfig.call_count == (1 if local else 0)
        if local:
            assert kubeconfig.call_args.kwargs["context"] == "local"
            assert kubeconfig.call_args.kwargs["config_file"] == "local.yaml"
    finally:
        jobs.close()


def test_incluster_failure_never_falls_back_to_local_credentials(monkeypatch):
    monkeypatch.setattr(kubernetes_jobs.config, "load_incluster_config", Mock(side_effect=RuntimeError("missing token")))
    local = Mock()
    monkeypatch.setattr(kubernetes_jobs.config, "load_kube_config", local)
    with pytest.raises(RuntimeError, match="missing token"):
        kubernetes_jobs.KubernetesJobs.connect(None, None, Mock(), "test")
    local.assert_not_called()
