"""Explicit single-record Kafka consumption and synchronous offset commits."""

from dataclasses import dataclass

from confluent_kafka import Consumer, KafkaException, TopicPartition

from mic3_api.application.runs.consumption import RecordLocation
from mic3_api.core.kafka_config import KafkaSettings
from mic3_api.infrastructure.kafka_security import security_config


@dataclass(frozen=True)
class KafkaRecord:
    location: RecordLocation
    key: bytes | None
    value: bytes | None


class KafkaRequestConsumer:
    def __init__(self, bootstrap_servers: str, topic: str, group: str,
                 *, security: KafkaSettings | None = None) -> None:
        self._consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": group,
            "client.id": "mic3.execution.worker",
            **security_config(security),
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
            "allow.auto.create.topics": False,
            "max.poll.interval.ms": 300000,
            "session.timeout.ms": 10000,
            "heartbeat.interval.ms": 3000,
            "socket.timeout.ms": 10000,
            "log_level": 0,
        })
        try:
            metadata = self._consumer.list_topics(topic=topic, timeout=10)
            if metadata.topics[topic].error is not None:
                raise KafkaException(metadata.topics[topic].error)
            self._consumer.subscribe([topic])
        except Exception:
            self._consumer.close()
            raise

    def poll(self, timeout: float) -> KafkaRecord | None:
        message = self._consumer.poll(timeout)
        if message is None:
            return None
        if message.error():
            raise KafkaException(message.error())
        return KafkaRecord(RecordLocation(message.topic(), message.partition(), message.offset()),
                           message.key(), message.value())

    def commit(self, record: KafkaRecord) -> None:
        location = record.location
        offsets = self._consumer.commit(offsets=[TopicPartition(
            location.topic, location.partition, location.offset + 1,
        )], asynchronous=False)
        for offset in offsets:
            if offset.error is not None:
                raise KafkaException(offset.error)

    def close(self) -> None:
        # Auto-commit and auto-store are both disabled, including during close.
        self._consumer.close()
