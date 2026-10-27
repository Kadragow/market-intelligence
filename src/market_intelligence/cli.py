"""Command-line entry point: ``mi``."""

from __future__ import annotations

import logging
import sys
import warnings

import typer
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from market_intelligence.config import ConfigError, Settings
from market_intelligence.pipeline import run_weekly
from market_intelligence.storage.db import (
    create_db_engine,
    current_revision,
    head_revision,
    upgrade,
)
from market_intelligence.storage.sql import SqlRepository

# DECISION: CLI built with typer (small dependency, typed options, built-in help).
app = typer.Typer(no_args_is_help=True, add_completion=False)

EXIT_REPORT_FAILED = 1
EXIT_CONFIG_ERROR = 2
EXIT_EMAIL_FAILED = 3
EXIT_STORAGE_FAILED = 4

db_app = typer.Typer(no_args_is_help=True, help="Database: migrations and status.")
app.add_typer(db_app, name="db")


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging.")) -> None:
    """Market Intelligence: research and weekly reporting. Never places orders."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Third-party request logs are noisy and may include full URLs.
    for name in ("httpx", "yfinance", "peewee", "alembic"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # yfinance internals trigger pandas deprecation warnings that are not actionable here.
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="yfinance")
    warnings.filterwarnings("ignore", category=FutureWarning, module="yfinance")


@app.command()
def weekly(
    mock: bool = typer.Option(
        False, "--mock", help="Use synthetic offline data and a fake model (no API keys)."
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--send",
        help="Dry-run (default) never sends e-mail. --send also requires MI_EMAIL_ENABLED=true "
        "and complete SMTP settings.",
    ),
) -> None:
    """Generate the weekly report, save it to reports/ and optionally e-mail it."""
    try:
        settings = Settings()
        result = run_weekly(settings, mock=mock, dry_run=dry_run)
    except ConfigError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc

    typer.echo(f"Markdown: {result.markdown_path}")
    typer.echo(f"HTML:     {result.html_path}")
    typer.echo(f"E-mail:   {result.send_reason}")
    typer.echo(f"Run id:   {result.run_id or 'not recorded'}")
    for error in result.storage_errors:
        typer.echo(f"Storage error: {error}", err=True)
    if not result.report_ok:
        typer.echo("Report FAILED validation; see the -FAILED report for details.", err=True)
        raise typer.Exit(EXIT_REPORT_FAILED)
    if result.email_error:
        raise typer.Exit(EXIT_EMAIL_FAILED)
    if result.storage_errors:
        raise typer.Exit(EXIT_STORAGE_FAILED)


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply database migrations (creates the SQLite file if needed)."""
    engine = create_db_engine(Settings().database_url)
    try:
        upgrade(engine)
    except SQLAlchemyError as exc:
        typer.echo(f"Migration failed: {type(exc).__name__}", err=True)
        raise typer.Exit(EXIT_STORAGE_FAILED) from exc
    typer.echo(f"Database at revision {current_revision(engine)} ({_safe_db_url(engine)})")


@db_app.command("status")
def db_status(limit: int = typer.Option(5, help="How many recent runs to show.")) -> None:
    """Show schema revision and the most recent runs."""
    engine = create_db_engine(Settings().database_url)
    try:
        current = current_revision(engine)
    except SQLAlchemyError as exc:
        typer.echo(f"Database error: {type(exc).__name__}", err=True)
        raise typer.Exit(EXIT_STORAGE_FAILED) from exc
    head = head_revision()
    typer.echo(f"Database: {_safe_db_url(engine)}")
    typer.echo(f"Schema:   {current or 'not initialised'} (code expects {head})")
    if current != head:
        typer.echo("Run `uv run mi db upgrade`.")
        return
    for run in SqlRepository(engine).recent_runs(limit):
        typer.echo(
            f"{run.started_at:%Y-%m-%d %H:%M} UTC  {run.report_week}  {run.mode:<4}  "
            f"{run.status:<7}  {run.model or '-'}  rules {run.rules_version}"
        )


def _safe_db_url(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=True)
