from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from mic3_api.core.config import Settings
from mic3_api.infrastructure.persistence import (
    Base,
    Role,
    User,
    UserIdentity,
    UserRole,
)


APPLICATION_TABLES = {"users", "roles", "user_identities", "user_roles"}
MEMBER_DESCRIPTION = "Default non-elevated MIC3 member role."


def test_upgrade_creates_schema_matching_model_metadata(
    alembic_config: Config,
    postgres_test_settings: Settings,
) -> None:
    command.upgrade(alembic_config, "head")
    engine = create_engine(postgres_test_settings.database_url, poolclass=NullPool)

    try:
        database_inspector = inspect(engine)

        assert set(Base.metadata.tables) == APPLICATION_TABLES
        assert APPLICATION_TABLES <= set(database_inspector.get_table_names())
        assert database_inspector.get_pk_constraint("user_identities")[
            "constrained_columns"
        ] == ["issuer", "subject"]
        assert database_inspector.get_pk_constraint("user_roles")[
            "constrained_columns"
        ] == ["user_id", "role_name"]
        identity_indexes = {
            index["name"]: index
            for index in database_inspector.get_indexes("user_identities")
        }
        assert identity_indexes["ix_user_identities_user_id"]["column_names"] == [
            "user_id"
        ]
        assert identity_indexes["ix_user_identities_user_id"]["unique"] is False
        command.check(alembic_config)
    finally:
        engine.dispose()


def test_member_is_the_only_seeded_role_and_upgrade_is_idempotent(
    migrated_engine: Engine,
    alembic_config: Config,
) -> None:
    command.upgrade(alembic_config, "head")

    with migrated_engine.connect() as connection:
        roles = connection.execute(
            select(Role.name, Role.description).order_by(Role.name)
        ).all()

    assert [tuple(role) for role in roles] == [("member", MEMBER_DESCRIPTION)]


def test_downgrade_removes_all_application_tables(
    alembic_config: Config,
    postgres_test_settings: Settings,
) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    engine = create_engine(postgres_test_settings.database_url, poolclass=NullPool)

    try:
        assert APPLICATION_TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_identity_keys_are_exact_and_scoped_by_issuer(
    migrated_engine: Engine,
) -> None:
    first_user_id = uuid4()
    second_user_id = uuid4()
    issuer = "HTTPS://Identity.Example/Realm "
    other_issuer = "https://identity.example/realm"
    subject = "Opaque-Subject-AbC 123"

    with migrated_engine.begin() as connection:
        connection.execute(
            User.__table__.insert(),
            [{"id": first_user_id}, {"id": second_user_id}],
        )
        connection.execute(
            UserIdentity.__table__.insert(),
            [
                {
                    "issuer": issuer,
                    "subject": subject,
                    "user_id": first_user_id,
                },
                {
                    "issuer": other_issuer,
                    "subject": subject,
                    "user_id": second_user_id,
                },
            ],
        )

    with migrated_engine.connect() as connection:
        stored_identities = connection.execute(
            select(UserIdentity.issuer, UserIdentity.subject).order_by(
                UserIdentity.issuer
            )
        ).all()

    assert {tuple(identity) for identity in stored_identities} == {
        (issuer, subject),
        (other_issuer, subject),
    }

    with pytest.raises(IntegrityError):
        with migrated_engine.begin() as connection:
            connection.execute(
                UserIdentity.__table__.insert(),
                {
                    "issuer": issuer,
                    "subject": subject,
                    "user_id": second_user_id,
                },
            )


def test_users_allow_duplicate_and_null_emails_without_implicit_roles(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session, session.begin():
        first_user = User(email="member@example.org")
        second_user = User(email="member@example.org")
        third_user = User(email=None)
        session.add_all([first_user, second_user, third_user])
        session.flush()

        users = [first_user, second_user, third_user]
        assert all(isinstance(user.id, UUID) for user in users)
        assert all(user.is_active is True for user in users)
        assert all(user.created_at.tzinfo is not None for user in users)
        assert all(user.updated_at.tzinfo is not None for user in users)

    with migrated_engine.connect() as connection:
        assignment_count = connection.scalar(
            select(func.count()).select_from(UserRole)
        )

    assert assignment_count == 0


def test_user_role_primary_key_rejects_duplicate_assignments(
    migrated_engine: Engine,
) -> None:
    user_id = uuid4()

    with migrated_engine.begin() as connection:
        connection.execute(User.__table__.insert(), {"id": user_id})
        connection.execute(
            UserRole.__table__.insert(),
            {"user_id": user_id, "role_name": "member"},
        )

    with pytest.raises(IntegrityError):
        with migrated_engine.begin() as connection:
            connection.execute(
                UserRole.__table__.insert(),
                {"user_id": user_id, "role_name": "member"},
            )


def test_identity_and_role_foreign_keys_are_enforced(
    migrated_engine: Engine,
) -> None:
    existing_user_id = uuid4()
    missing_user_id = uuid4()

    with migrated_engine.begin() as connection:
        connection.execute(User.__table__.insert(), {"id": existing_user_id})

    with pytest.raises(IntegrityError):
        with migrated_engine.begin() as connection:
            connection.execute(
                UserIdentity.__table__.insert(),
                {
                    "issuer": "https://identity.example/realm",
                    "subject": "missing-user",
                    "user_id": missing_user_id,
                },
            )

    with pytest.raises(IntegrityError):
        with migrated_engine.begin() as connection:
            connection.execute(
                UserRole.__table__.insert(),
                {"user_id": missing_user_id, "role_name": "member"},
            )

    with pytest.raises(IntegrityError):
        with migrated_engine.begin() as connection:
            connection.execute(
                UserRole.__table__.insert(),
                {"user_id": existing_user_id, "role_name": "admin"},
            )
