"""Acceptance settings shared by both worker modes."""

from pydantic import Field

from mic3_api.core.kafka_config import KafkaSettings


class WorkerSettings(KafkaSettings):
    kafka_consumer_group: str = Field(default="mic3.execution.worker.v1",
                                     pattern=r"^[a-z0-9]+(?:\.[a-z0-9]+)+$", max_length=249)
    worker_retry_seconds: float = Field(default=2, gt=0, le=60)
    worker_idle_timeout_seconds: float = Field(default=10, gt=0, le=60)
    worker_statement_timeout_ms: int = Field(default=10000, ge=100, le=20000)
    worker_lock_timeout_ms: int = Field(default=5000, ge=100, le=20000)
