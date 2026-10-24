"""Exercise the actual local broker in an isolated Compose project and volume."""

import json
from uuid import uuid4


def test_local_kafka_topics_replay_and_persistent_recreation(kafka_broker):
    run, kafka = kafka_broker.run, kafka_broker.kafka
    run("run", "--rm", "kafka-init")
    # Repeated initialization must preserve the existing topics.
    run("run", "--rm", "kafka-init")
    for topic in ("mic3.run.requests.v1", "mic3.run.events.v1"):
        description = kafka("kafka-topics", "--describe", "--topic", topic)
        assert "PartitionCount: 1" in description
        assert "ReplicationFactor: 1" in description
        assert "retention.ms=604800000" in description
        assert "retention.bytes=1073741824" in description

    payload = json.dumps({"event_id": str(uuid4()), "run_id": str(uuid4())})
    record = f"test-run:{payload}"
    topic = "mic3.test." + uuid4().hex
    kafka("kafka-topics", "--create", "--topic", topic,
          "--partitions", "1", "--replication-factor", "1")
    kafka("kafka-console-producer", "--topic", topic,
          "--property", "parse.key=true", "--property", "key.separator=:",
          "--producer-property", "acks=all", input=record + "\n")

    def consume(group):
        output = kafka("kafka-console-consumer", "--topic", topic,
                       "--group", group, "--from-beginning", "--max-messages", "1",
                       "--timeout-ms", "20000", "--property", "print.key=true",
                       "--property", "key.separator=:")
        assert record in output.splitlines()

    consume("first.reader")
    consume("independent.reader")
    before = run("exec", "-T", "kafka", "cat", "/var/lib/kafka/data/meta.properties")
    # Stronger than a process restart: replace the container but keep its volume.
    run("up", "-d", "--force-recreate", "--wait", "kafka", timeout=240)
    after = run("exec", "-T", "kafka", "cat", "/var/lib/kafka/data/meta.properties")
    assert before == after
    consume("reader.after.recreation")
