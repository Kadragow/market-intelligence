"""Command-line entry point: ``mi``."""

from __future__ import annotations

import logging
import sys

import typer

from market_intelligence.config import ConfigError, Settings
from market_intelligence.pipeline import run_weekly

# DECISION: CLI built with typer (small dependency, typed options, built-in help).
app = typer.Typer(no_args_is_help=True, add_completion=False)

EXIT_REPORT_FAILED = 1
EXIT_CONFIG_ERROR = 2
EXIT_EMAIL_FAILED = 3


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging.")) -> None:
    """Market Intelligence: research and weekly reporting. Never places orders."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Third-party request logs are noisy and may include full URLs.
    for name in ("httpx", "yfinance", "peewee"):
        logging.getLogger(name).setLevel(logging.WARNING)


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
    if not result.report_ok:
        typer.echo("Report FAILED validation; see the -FAILED report for details.", err=True)
        raise typer.Exit(EXIT_REPORT_FAILED)
    if result.email_error:
        raise typer.Exit(EXIT_EMAIL_FAILED)
