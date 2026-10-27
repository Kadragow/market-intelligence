"""Alembic environment. Uses the connection passed in ``config.attributes`` when present."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, create_engine

from market_intelligence.storage.models import Base

config = context.config
target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER most things; batch mode only applies there.
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url is not configured")
    engine = create_engine(url)
    with engine.begin() as conn:
        _run(conn)


if context.is_offline_mode():
    raise RuntimeError("offline (SQL script) migrations are not supported")
run_migrations_online()
