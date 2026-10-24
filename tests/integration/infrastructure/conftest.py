"""A real broker isolated from the developer's Compose project."""

import os
from pathlib import Path
import shutil
import socket
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def kafka_broker():
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker required for Kafka integration")
    project = "mic3-kafka-test-" + uuid4().hex[:12]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {**os.environ, "KAFKA_PORT": str(port)}
    compose = [docker, "compose", "--env-file", str(ROOT / ".env.example"),
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
        yield SimpleNamespace(run=run, kafka=kafka, address=f"127.0.0.1:{port}")
    finally:
        subprocess.run([*compose, "down", "--volumes", "--remove-orphans"],
                       cwd=ROOT, env=environment, capture_output=True,
                       text=True, timeout=120, check=True)
