"""Map validated TLS settings to librdkafka without weakening verification."""
from mic3_api.core.kafka_config import KafkaSettings


def security_config(settings: KafkaSettings | None) -> dict:
    if settings is None or settings.kafka_security_protocol == "PLAINTEXT":
        return {"security.protocol": "PLAINTEXT"}
    return {
        "security.protocol": "SSL",
        "ssl.ca.location": str(settings.kafka_ssl_ca_file),
        "ssl.certificate.location": str(settings.kafka_ssl_certificate_file),
        "ssl.key.location": str(settings.kafka_ssl_key_file),
        "enable.ssl.certificate.verification": True,
        "ssl.endpoint.identification.algorithm": "https",
    }
