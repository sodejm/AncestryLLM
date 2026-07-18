"""Alembic environment for programmatic SQLCipher migrations."""

from __future__ import annotations

from alembic import context

from ancestryllm.storage.models import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    raise RuntimeError("Offline plaintext migration scripts are intentionally unsupported.")


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("An authenticated SQLCipher connection is required for migrations.")
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
