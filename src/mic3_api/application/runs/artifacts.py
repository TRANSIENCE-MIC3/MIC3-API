"""Output contracts are independent of S3 and scientific model formats."""
from typing import Protocol
from mic3_api.application.runs.execution import Artifact, ExecutionFailure, ExecutionWork


class ArtifactInventory(Protocol):
    def list(self, prefix: str) -> tuple[Artifact, ...]: ...


class VerifyOutputs:
    def __init__(self, inventory: ArtifactInventory):
        self.inventory = inventory

    def collect(self, work: ExecutionWork) -> tuple[Artifact, ...]:
        artifacts = self.inventory.list(work.output_prefix)
        sizes = {a.object_key: a.size_bytes for a in artifacts}
        required = work.release.execution_definition.resolve(work.parameters).output_requirements.required_files
        if required and any(sizes.get(work.output_prefix + path, 0) <= 0 for path in required):
            raise ExecutionFailure("required_outputs_missing", artifacts)
        if not any(a.size_bytes > 0 for a in artifacts):
            raise ExecutionFailure("outputs_empty", artifacts)
        return artifacts
