"""Configuration for acceptance only; execution settings belong to later work."""

from pydantic import Field

from mic3_api.core.config import DatabaseSettings


class WorkerSettings(DatabaseSettings):
    kafka_bootstrap_servers: str = Field(default="127.0.0.1:9092", min_length=1)
    kafka_requests_topic: str = Field(default="mic3.run.requests.v1",
                                      pattern=r"^[a-z0-9]+(?:\.[a-z0-9]+)+$", max_length=249)
    kafka_consumer_group: str = Field(default="mic3.execution.worker.v1",
                                     pattern=r"^[a-z0-9]+(?:\.[a-z0-9]+)+$", max_length=249)
    worker_retry_seconds: float = Field(default=2, gt=0, le=60)
    worker_idle_timeout_seconds: float = Field(default=10, gt=0, le=60)
    worker_statement_timeout_ms: int = Field(default=10000, ge=100, le=20000)
    worker_lock_timeout_ms: int = Field(default=5000, ge=100, le=20000)
