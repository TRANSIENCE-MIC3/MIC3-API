"""Host worker execution configuration, kept separate from HTTP dependencies."""
from pathlib import Path
from pydantic import Field, SecretStr
from mic3_api.core.worker_config import WorkerSettings


class ExecutionSettings(WorkerSettings):
    execution_kubeconfig: Path
    execution_context: str = Field(min_length=1)
    execution_namespace: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    execution_chart: Path = Path("deploy/helm/model-run")
    execution_values_file: Path | None = None
    execution_capacity: int = Field(default=1, ge=1, le=100)
    execution_poll_seconds: float = Field(default=2, gt=0, le=60)
    artifact_endpoint: str = Field(pattern=r"^https?://")
    artifact_bucket: str = Field(min_length=1)
    artifact_access_key: SecretStr
    artifact_secret_key: SecretStr
