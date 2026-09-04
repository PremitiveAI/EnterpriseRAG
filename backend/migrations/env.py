"""Alembic environment.

The database URL comes from ``config.settings``, never from alembic.ini, so
there is one source of configuration.

The URL is passed straight to ``create_engine`` rather than through
``config.set_main_option``. configparser applies ``BasicInterpolation`` to
values it stores, so a URL containing a percent-encoded character — a
password with ``%40`` for ``@``, for example — raises
``ValueError: invalid interpolation syntax``. Bypassing the ini layer avoids
the problem entirely instead of escaping around it.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# Importing the registry pulls in every model so autogenerate sees full metadata.
from app.models_registry import Base
from config.settings import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(settings.DATABASE_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
