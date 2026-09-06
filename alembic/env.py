from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from mic3_api.core.config import DatabaseSettings
from mic3_api.infrastructure.persistence import Base


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Configure an offline migration context without opening a connection."""
    context.configure(
        url=DatabaseSettings().database_url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Configure an online migration context using application settings."""
    connectable = create_engine(
        DatabaseSettings().database_url,
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 3},
    )

    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
