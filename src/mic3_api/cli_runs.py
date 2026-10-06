"""Operator CLI transport for catalog registration and run submission."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from mic3_api.application.models.catalog import ModelError, register_release
from mic3_api.application.runs.submission import SubmitRun
from mic3_api.application.runs.fingerprint import fingerprint
from mic3_api.infrastructure.model_jobs import model_job_values
from mic3_api.infrastructure.persistence.model_catalog import SqlAlchemyModelCatalog
from mic3_api.infrastructure.persistence.run_submissions import SqlAlchemyRunSubmissions


def add_run_commands(commands) -> None:
    register = commands.add_parser("register-model-release", help="Register or update a trusted model/image catalog entry")
    register.add_argument("--config", type=Path, required=True)
    submit = commands.add_parser("submit-run", help="Persist queued work (no execution yet)")
    submit.add_argument("--release-id", type=UUID, required=True)
    submit.add_argument("--parameters", type=Path, required=True)
    submit.add_argument("--requested-by", type=UUID)
    values = commands.add_parser("model-job-values", help="Export catalog execution as Helm Job values")
    values.add_argument("--run-id", type=UUID, required=True)
    show = commands.add_parser("show-run", help="Inspect a persisted run and outbox publication state")
    show.add_argument("--run-id", type=UUID, required=True)


def read_json(path: Path) -> object:
    def reject_constant(value: str):
        raise ValueError("Non-finite JSON number")

    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject_constant)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ModelError("Cannot read a valid JSON input file.") from exc


def execute_run_command(args: argparse.Namespace, session: Session) -> dict:
    catalog = SqlAlchemyModelCatalog(session)
    submissions = SqlAlchemyRunSubmissions(session)
    if args.command == "register-model-release":
        release = register_release(read_json(args.config), catalog)
        return {"id": release.id, "model_id": release.definition.model_id,
                "image_digest": release.definition.image_digest}
    if args.command == "submit-run":
        return asdict(SubmitRun(catalog, submissions).execute(
            args.release_id, read_json(args.parameters), args.requested_by,
        ))
    if args.command == "model-job-values":
        run = submissions.show(args.run_id)
        release = catalog.get_release(run["model_release_id"])
        if run["status"] != "queued":
            raise ModelError("Only queued runs can be exported; retry by submitting a new run.")
        if fingerprint(release.definition, run["parameters"]) != run["fingerprint"]:
            raise ModelError("Catalog execution has changed; submit a new run before exporting Job values.")
        return model_job_values(release.definition, run["parameters"], args.run_id)
    return submissions.show(args.run_id)
