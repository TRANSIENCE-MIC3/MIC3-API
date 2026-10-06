"""Operator-only MIC3 commands using database credentials from the environment."""

import argparse
import json
import sys
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from mic3_api.application.users.administration import AdministrationError, GrantAdmin
from mic3_api.application.models.catalog import ModelError
from mic3_api.cli_runs import add_run_commands, execute_run_command
from mic3_api.core.config import DatabaseSettings
from mic3_api.infrastructure.database import Database
from mic3_api.infrastructure.persistence.user_administration import (
    SqlAlchemyUserAdministrationUnitOfWork,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MIC3 operator commands")
    commands = parser.add_subparsers(dest="command", required=True)
    grant = commands.add_parser("grant-admin", help="Grant an existing active user admin")
    grant.add_argument("--user-id", type=UUID, required=True)
    add_run_commands(commands)
    args = parser.parse_args(argv)
    database = None
    try:
        settings = DatabaseSettings()
        database = Database(settings.database_url)
        with database.open_session() as session:
            if args.command == "grant-admin":
                granted = GrantAdmin().execute(
                    args.user_id, SqlAlchemyUserAdministrationUnitOfWork(session)
                )
                result = f"{args.user_id}: admin {'granted' if granted else 'already present'}"
            else:
                result = json.dumps(execute_run_command(args, session), default=str, allow_nan=False)
        print(result)
        return 0
    except (AdministrationError, ModelError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (ValidationError, SQLAlchemyError):
        print("Database configuration or operation failed.", file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
