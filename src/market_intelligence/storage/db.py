"""Engine creation, migrations and schema checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
IN_MEMORY_URL = "sqlite://"


class SchemaNotCurrentError(Exception):
    """The database schema is missing or behind the code's migrations."""


def create_db_engine(url: str) -> Engine:
    parsed = make_url(url)
    kwargs: dict[str, Any] = {}
    if parsed.get_backend_name() == "sqlite":
        database = parsed.database
        if database in (None, "", ":memory:"):
            # One shared connection, otherwise every connection gets its own empty database.
            kwargs.update(poolclass=StaticPool, connect_args={"check_same_thread": False})
        else:
            Path(database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    if parsed.get_backend_name() == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # Percent signs must be escaped for configparser.
    cfg.set_main_option(
        "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
    )
    return cfg


def upgrade(engine: Engine) -> None:
    cfg = alembic_config(engine)
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str | None:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return ScriptDirectory.from_config(cfg).get_current_head()


def ensure_schema_current(engine: Engine) -> None:
    current, head = current_revision(engine), head_revision()
    if current != head:
        raise SchemaNotCurrentError(
            f"database schema is at {current or 'no revision'}, code expects {head}; "
            "run `uv run mi db upgrade`"
        )
