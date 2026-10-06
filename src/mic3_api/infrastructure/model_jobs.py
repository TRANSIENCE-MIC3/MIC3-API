"""Translate a catalog invocation into values for the existing manual Job chart."""

from uuid import UUID

from mic3_api.application.models.catalog import ReleaseDefinition


def model_job_values(release: ReleaseDefinition, parameters: dict, run_id: UUID) -> dict:
    effective = release.execution_definition.prepare(parameters)
    invocation = release.execution_definition.resolve(effective)
    return {"runId": str(run_id), "model": {
        "id": release.model_id, "mode": effective["mode"], "image": release.image_digest,
        "command": list(invocation.arguments), "workingDir": invocation.working_directory,
        "outputPath": invocation.output_directory,
    }}
