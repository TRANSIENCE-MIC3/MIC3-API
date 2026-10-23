"""Shared connection settings for database-backed Kafka processes."""
from typing import Literal

from pydantic import Field, FilePath, model_validator

from mic3_api.core.config import DatabaseSettings


class KafkaSettings(DatabaseSettings):
    kafka_bootstrap_servers: str = Field(default="127.0.0.1:9092", min_length=1)
    kafka_requests_topic: str = Field(default="mic3.run.requests.v1",
                                      pattern=r"^[a-z0-9]+(?:\.[a-z0-9]+)+$", max_length=249)
    kafka_security_protocol: Literal["PLAINTEXT", "SSL"] = "PLAINTEXT"
    kafka_ssl_ca_file: FilePath | None = None
    kafka_ssl_certificate_file: FilePath | None = None
    kafka_ssl_key_file: FilePath | None = None

    @model_validator(mode="after")
    def validate_tls_identity(self):
        files = (self.kafka_ssl_ca_file, self.kafka_ssl_certificate_file, self.kafka_ssl_key_file)
        if self.kafka_security_protocol == "SSL" and not all(files):
            raise ValueError("SSL requires CA, client certificate and private key files")
        if self.kafka_security_protocol == "PLAINTEXT" and any(files):
            raise ValueError("TLS files require KAFKA_SECURITY_PROTOCOL=SSL")
        return self
