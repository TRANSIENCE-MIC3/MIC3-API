"""Kafka transport and version-one JSON encoding, outside application logic."""

import json
import time

from confluent_kafka import KafkaException, Producer

from mic3_api.application.runs.publication import PublicationError, RunEvent


class KafkaEventPublisher:
    def __init__(self, bootstrap_servers: str, requests_topic: str,
                 delivery_timeout_seconds: int = 10) -> None:
        self._topic = requests_topic
        self._timeout = delivery_timeout_seconds
        self._producer = Producer({
            "bootstrap.servers": bootstrap_servers,
            "client.id": "mic3.outbox.publisher",
            "security.protocol": "PLAINTEXT",
            "enable.idempotence": True,
            "acks": "all",
            "message.timeout.ms": delivery_timeout_seconds * 1000,
            "socket.timeout.ms": delivery_timeout_seconds * 1000,
            "allow.auto.create.topics": False,
            # Report bounded application errors rather than native endpoint logs.
            "log_level": 0,
        })

    def publish(self, event: RunEvent) -> None:
        if event.event_type != "run.requested":
            raise PublicationError("Unsupported outbox event type.")
        payload = json.dumps({
            "schema_version": 1,
            "event_id": str(event.id),
            "run_id": str(event.run_id),
            "event_type": event.event_type,
            "created_at": event.created_at.isoformat(),
        }, separators=(",", ":"), allow_nan=False).encode("utf-8")
        delivery = []

        def acknowledged(error, message):
            delivery.append(error)

        try:
            self._producer.produce(
                self._topic, key=str(event.run_id).encode("utf-8"), value=payload,
                on_delivery=acknowledged,
            )
            deadline = time.monotonic() + self._timeout + 1
            while not delivery:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PublicationError("Kafka acknowledgement timed out.")
                self._producer.poll(min(0.2, remaining))
        except (KafkaException, BufferError) as exc:
            raise PublicationError("Kafka publication failed.") from exc
        if delivery[0] is not None:
            raise PublicationError(f"Kafka delivery was not acknowledged ({delivery[0].name()}).")

    def close(self) -> None:
        # Late delivery is safe: an unconfirmed event remains in the outbox.
        self._producer.flush(self._timeout + 1)
