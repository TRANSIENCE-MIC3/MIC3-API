"""Operator-only MIC3 commands using database credentials from the environment."""

import argparse
import sys
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from mic3_api.application.users.administration import AdministrationError, GrantAdmin
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
    args = parser.parse_args(argv)
    database = None
    try:
        settings = DatabaseSettings()
        database = Database(settings.database_url)
        with database.open_session() as session:
            granted = GrantAdmin().execute(
                args.user_id, SqlAlchemyUserAdministrationUnitOfWork(session)
            )
        print(f"{args.user_id}: admin {'granted' if granted else 'already present'}")
        return 0
    except AdministrationError as exc:
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
