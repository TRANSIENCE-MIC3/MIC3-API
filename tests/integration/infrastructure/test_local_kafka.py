"""Exercise the actual local broker in an isolated Compose project and volume."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOCKER = shutil.which("docker")
pytestmark = pytest.mark.skipif(not DOCKER, reason="Docker required for Kafka integration")


def test_local_kafka_topics_replay_and_persistent_recreation():
    project = "mic3-kafka-test-" + uuid4().hex[:12]
    # Avoid colliding with the developer's running Compose services.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {**os.environ, "KAFKA_PORT": str(port)}
    compose = [DOCKER, "compose", "--env-file", str(ROOT / ".env.example"),
               "-f", str(ROOT / "compose.yaml"), "-p", project]

    def run(*args, input=None, timeout=120):
        result = subprocess.run(
            [*compose, *args], cwd=ROOT, env=environment, input=input,
            capture_output=True, text=True, timeout=timeout,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def kafka(tool, *args, input=None):
        return run("exec", "-T", "kafka", f"/opt/kafka/bin/{tool}.sh",
                   "--bootstrap-server", "kafka:19092", *args, input=input)

    try:
        run("up", "-d", "--wait", "kafka", timeout=240)
        run("run", "--rm", "kafka-init")
        # Repeated initialization must preserve the existing topics.
        run("run", "--rm", "kafka-init")
        for topic in ("mic3.run-requests.v1", "mic3.run-events.v1"):
            description = kafka("kafka-topics", "--describe", "--topic", topic)
            assert "PartitionCount: 1" in description
            assert "ReplicationFactor: 1" in description
            assert "retention.ms=604800000" in description
            assert "retention.bytes=1073741824" in description

        payload = json.dumps({"event_id": str(uuid4()), "run_id": str(uuid4())})
        record = f"test-run:{payload}"
        topic = "mic3.run-requests.v1"
        kafka("kafka-console-producer", "--topic", topic,
              "--property", "parse.key=true", "--property", "key.separator=:",
              "--producer-property", "acks=all", input=record + "\n")

        def consume(group):
            output = kafka("kafka-console-consumer", "--topic", topic,
                           "--group", group, "--from-beginning", "--max-messages", "1",
                           "--timeout-ms", "20000", "--property", "print.key=true",
                           "--property", "key.separator=:")
            assert record in output.splitlines()

        consume("first-reader")
        consume("independent-reader")
        before = run("exec", "-T", "kafka", "cat", "/var/lib/kafka/data/meta.properties")
        # Stronger than a process restart: replace the container but keep its volume.
        run("up", "-d", "--force-recreate", "--wait", "kafka", timeout=240)
        after = run("exec", "-T", "kafka", "cat", "/var/lib/kafka/data/meta.properties")
        assert before == after
        consume("reader-after-recreation")
    finally:
        # Only this uniquely named disposable test project is removed.
        subprocess.run([*compose, "down", "--volumes", "--remove-orphans"],
                       cwd=ROOT, env=environment, capture_output=True,
                       text=True, timeout=120, check=True)
