"""Render the shared chart with catalog values taking precedence over operations."""
import json
from pathlib import Path
import subprocess

import yaml

from mic3_api.application.runs.execution import InfrastructureUnavailable
from mic3_api.infrastructure.model_jobs import model_job_values


class HelmJobRenderer:
    def __init__(self, chart: Path, values_file: Path | None = None, binary: str = "helm"):
        self.chart, self.values_file, self.binary = chart, values_file, binary

    def render(self, work):
        args = [self.binary, "template", work.job_name, str(self.chart), "--namespace", work.namespace]
        if self.values_file:
            args += ["-f", str(self.values_file)]
        args += ["-f", "-"]
        try:
            result = subprocess.run(args, input=json.dumps(model_job_values(work.release, work.parameters, work.id)),
                                    text=True, capture_output=True, timeout=15, check=True)
            documents = list(yaml.safe_load_all(result.stdout))
        except (OSError, subprocess.SubprocessError, yaml.YAMLError) as exc:
            raise InfrastructureUnavailable("Job rendering failed.") from exc
        if len(documents) != 1 or documents[0].get("kind") != "Job":
            raise InfrastructureUnavailable("Expected one rendered Job.")
        return documents[0]
