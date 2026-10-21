"""Settings for the separate publisher; no OIDC configuration is required."""

from pydantic import Field

from mic3_api.core.config import DatabaseSettings


class PublisherSettings(DatabaseSettings):
    kafka_bootstrap_servers: str = Field(default="127.0.0.1:9092", min_length=1)
    kafka_requests_topic: str = Field(
        default="mic3.run.requests.v1", pattern=r"^[a-z0-9]+(?:\.[a-z0-9]+)+$", max_length=249,
    )
    kafka_delivery_timeout_seconds: int = Field(default=10, ge=1, le=60)
    publisher_batch_size: int = Field(default=10, ge=1, le=100)
    publisher_poll_seconds: float = Field(default=2, gt=0, le=60)
