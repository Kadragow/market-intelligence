import logging
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from market_intelligence.ai.client import FakeLLMClient
from market_intelligence.cli import app
from market_intelligence.config import ConfigError, Settings
from market_intelligence.data.providers.fixtures import FixtureNewsProvider, FixturePriceProvider
from market_intelligence.delivery.email import EmailError, RecordingEmailSender
from market_intelligence.mock_data import MOCK_PRICES_DIR, MOCK_RSS_DIR
from market_intelligence.pipeline import iso_week, run_weekly

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "fixtures" / "golden"


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run in an empty directory with no .env and no MI_* / OPENROUTER_* variables."""
    for key in list(os.environ):
        if key.startswith(("MI_", "OPENROUTER_")):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MI_CONFIG_DIR", str(ROOT / "config"))
    monkeypatch.setenv("MI_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("MI_DATABASE_URL", f"sqlite:///{tmp_path / 'db' / 'mi.db'}")
    return tmp_path


def _smtp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MI_EMAIL_ENABLED", "true")
    monkeypatch.setenv("MI_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("MI_SMTP_USERNAME", "user")
    monkeypatch.setenv("MI_SMTP_PASSWORD", "smtp-super-secret")
    monkeypatch.setenv("MI_EMAIL_FROM", "bot@example.com")
    monkeypatch.setenv("MI_EMAIL_TO", "me@example.com")


def test_mock_run_produces_complete_report(isolated_env: Path) -> None:
    result = run_weekly(Settings(), mock=True, dry_run=True)
    assert result.report_ok
    assert result.sent is False
    assert result.markdown_path.name == "weekly-2026-W42-mock.md"
    md = result.markdown_path.read_text(encoding="utf-8")
    html = result.html_path.read_text(encoding="utf-8")
    for n in range(1, 12):
        assert f"## {n}. " in md
        assert f"<h2>{n}. " in html
    assert "Nie stanowi rekomendacji inwestycyjnej ani porady finansowej" in md
    assert "DANE TESTOWE" in md and "DANE TESTOWE" in html
    assert "niewystarczające dane" in md  # USD/PLN has no data
    assert "nieaktualne" in md  # US 10Y is stale
    assert '<meta charset="utf-8">' in html


def test_golden_files(isolated_env: Path) -> None:
    """Golden files for Markdown and HTML (UTF-8, Polish characters).

    Regenerate with: UPDATE_GOLDEN=1 uv run pytest tests/integration
    """
    result = run_weekly(Settings(), mock=True, dry_run=True)
    for path in (result.markdown_path, result.html_path):
        actual = path.read_bytes()
        golden = GOLDEN / path.name
        if os.environ.get("UPDATE_GOLDEN") == "1":
            golden.parent.mkdir(parents=True, exist_ok=True)
            golden.write_bytes(actual)
        assert golden.is_file(), f"missing golden file {golden}"
        assert actual == golden.read_bytes(), f"{path.name} differs from golden file"
        text = actual.decode("utf-8")
        assert "Złoto" in text
        assert "Źródła" in text
        assert "półprzewodników" in text


def test_rerun_is_idempotent(isolated_env: Path) -> None:
    first = run_weekly(Settings(), mock=True, dry_run=True)
    content = first.markdown_path.read_bytes()
    second = run_weekly(Settings(), mock=True, dry_run=True)
    assert second.markdown_path == first.markdown_path
    assert second.markdown_path.read_bytes() == content
    assert len(list(first.markdown_path.parent.iterdir())) == 2


def test_email_sent_only_when_all_conditions_met(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _smtp_env(monkeypatch)
    sender = RecordingEmailSender()
    result = run_weekly(Settings(), mock=True, dry_run=False, email_sender=sender)
    assert result.sent
    assert len(sender.sent) == 1
    message = sender.sent[0]
    assert message.subject == "[TEST] Tygodniowy brief rynkowy 2026-W42"
    assert message.recipients == ["me@example.com"]
    assert "Złoto" in message.html


def test_dry_run_never_sends(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _smtp_env(monkeypatch)
    sender = RecordingEmailSender()
    result = run_weekly(Settings(), mock=True, dry_run=True, email_sender=sender)
    assert sender.sent == []
    assert "dry-run" in result.send_reason


def test_not_enabled_never_sends(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _smtp_env(monkeypatch)
    monkeypatch.setenv("MI_EMAIL_ENABLED", "false")
    sender = RecordingEmailSender()
    result = run_weekly(Settings(), mock=True, dry_run=False, email_sender=sender)
    assert sender.sent == []
    assert "not enabled" in result.send_reason


def test_incomplete_smtp_never_sends(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _smtp_env(monkeypatch)
    monkeypatch.delenv("MI_SMTP_PASSWORD")
    sender = RecordingEmailSender()
    result = run_weekly(Settings(), mock=True, dry_run=False, email_sender=sender)
    assert sender.sent == []
    assert "MI_SMTP_PASSWORD" in result.send_reason


def test_smtp_failure_is_reported(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _smtp_env(monkeypatch)
    sender = RecordingEmailSender(error=EmailError("SMTP error: ConnectionRefusedError"))
    result = run_weekly(Settings(), mock=True, dry_run=False, email_sender=sender)
    assert result.report_ok
    assert not result.sent
    assert result.email_error == "SMTP error: ConnectionRefusedError"
    assert result.markdown_path.is_file()  # local copy saved before sending


def test_invalid_model_output_gives_failure_report_and_no_email(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _smtp_env(monkeypatch)
    sender = RecordingEmailSender()
    llm = FakeLLMClient(lambda s, u: '{"summary": "not a list"}')
    result = run_weekly(Settings(), mock=True, dry_run=False, llm=llm, email_sender=sender)
    assert not result.report_ok
    assert sender.sent == []
    assert result.markdown_path.name == "weekly-2026-W42-mock-FAILED.md"
    md = result.markdown_path.read_text(encoding="utf-8")
    assert "NIEPOWODZENIE" in md
    assert "schema:" in md


def test_model_inventing_numbers_is_rejected(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_intelligence import pipeline
    from market_intelligence.ai.mock_writer import mock_draft
    from market_intelligence.ai.schema import Claim

    captured: dict[str, Any] = {}
    original = pipeline.build_report_input

    def spy(**kwargs: Any) -> Any:
        captured["input"] = original(**kwargs)
        return captured["input"]

    def responder(system: str, user: str) -> str:
        draft = mock_draft(captured["input"])
        draft.summary.append(Claim(text="Indeks wzrośnie o 15%.", source_ids=["price-US_SPY"]))
        return draft.model_dump_json()

    monkeypatch.setattr(pipeline, "build_report_input", spy)
    result = run_weekly(Settings(), mock=True, dry_run=True, llm=FakeLLMClient(responder))
    assert not result.report_ok
    assert any("'15'" in e for e in result.errors)


def test_no_usable_data_skips_model(isolated_env: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    llm = FakeLLMClient(lambda s, u: "{}")
    result = run_weekly(
        Settings(),
        mock=True,
        dry_run=True,
        price_provider=FixturePriceProvider(empty),
        llm=llm,
    )
    assert not result.report_ok
    assert llm.calls == []
    assert result.errors == ["no instrument has sufficient, fresh price data"]


def test_real_mode_without_api_key_fails_clearly(isolated_env: Path) -> None:
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        run_weekly(
            Settings(),
            mock=False,
            dry_run=True,
            price_provider=FixturePriceProvider(MOCK_PRICES_DIR),
            news_provider=FixtureNewsProvider(MOCK_RSS_DIR),
        )


def test_secrets_never_logged_or_written(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _smtp_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-very-secret")
    caplog.set_level(logging.DEBUG)
    sender = RecordingEmailSender(error=EmailError("SMTP authentication failed (code 535)"))
    result = run_weekly(Settings(), mock=True, dry_run=False, email_sender=sender)
    outputs = [
        caplog.text,
        result.markdown_path.read_text(encoding="utf-8"),
        result.html_path.read_text(encoding="utf-8"),
        repr(result),
    ]
    for text in outputs:
        assert "sk-or-test-very-secret" not in text
        assert "smtp-super-secret" not in text


def test_iso_week_uses_warsaw_time() -> None:
    from datetime import UTC, datetime

    # Sunday 2026-10-18 23:30 UTC is already Monday in Warsaw (week 43).
    assert iso_week(datetime(2026, 10, 18, 23, 30, tzinfo=UTC)) == "2026-W43"
    assert iso_week(datetime(2026, 10, 18, 16, 0, tzinfo=UTC)) == "2026-W42"


def test_cli_mock_dry_run(isolated_env: Path) -> None:
    result = CliRunner().invoke(app, ["weekly", "--mock", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "weekly-2026-W42-mock.md" in result.output
    assert "dry-run" in result.output


def _spy_input(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from market_intelligence import pipeline

    captured: dict[str, Any] = {}
    original = pipeline.build_report_input

    def spy(**kwargs: Any) -> Any:
        captured["input"] = original(**kwargs)
        return captured["input"]

    monkeypatch.setattr(pipeline, "build_report_input", spy)
    return captured


def _mock_llm(captured: dict[str, Any]) -> FakeLLMClient:
    from market_intelligence.ai.mock_writer import mock_draft

    return FakeLLMClient(lambda s, u: mock_draft(captured["input"]).model_dump_json())


def test_macro_reaches_model_input_and_report(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _spy_input(monkeypatch)
    result = run_weekly(Settings(), mock=True, dry_run=True, llm=_mock_llm(captured))
    assert result.report_ok, result.errors
    macro = {m.series_id: m for m in captured["input"].macro}
    assert set(macro) == {"CPIAUCSL", "FEDFUNDS", "DGS10", "UNRATE"}
    assert macro["UNRATE"].data_status == "stale"
    assert macro["CPIAUCSL"].yoy_change_pct is not None
    md = result.markdown_path.read_text(encoding="utf-8")
    assert "| Wskaźnik | Ostatnia wartość |" in md
    assert "This product uses the FRED" not in md  # fixture data, not FRED


def test_real_mode_without_fred_key_marks_macro_missing(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_intelligence.pipeline import MOCK_NOW

    _upgraded_db()
    captured = _spy_input(monkeypatch)
    result = run_weekly(
        Settings(),
        mock=False,
        dry_run=True,
        now=MOCK_NOW,
        price_provider=FixturePriceProvider(MOCK_PRICES_DIR),
        news_provider=FixtureNewsProvider(MOCK_RSS_DIR),
        llm=_mock_llm(captured),
    )
    assert result.report_ok, result.errors
    statuses = {m.data_status for m in captured["input"].macro}
    assert statuses == {"insufficient_data"}
    html = result.html_path.read_text(encoding="utf-8")
    assert "brak klucza FRED_API_KEY" in html


def test_fred_notice_shown_for_fred_data(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_intelligence.data.providers.fixtures import FixtureMacroProvider
    from market_intelligence.mock_data import MOCK_FRED_DIR

    class FredLike(FixtureMacroProvider):
        name = "fred"

    result = run_weekly(Settings(), mock=True, dry_run=True, macro_provider=FredLike(MOCK_FRED_DIR))
    md = result.markdown_path.read_text(encoding="utf-8")
    html = result.html_path.read_text(encoding="utf-8")
    notice = "This product uses the FRED® API but is not endorsed or certified"
    assert notice in md and notice in html
    assert "https://fred.stlouisfed.org/series/CPIAUCSL" in md


def test_sources_list_only_cited_headlines(isolated_env: Path) -> None:
    result = run_weekly(Settings(), mock=True, dry_run=True)
    md = result.markdown_path.read_text(encoding="utf-8")
    # The mock writer cites 4 of the 5 synthetic headlines (not the central bank one).
    assert "Przeanalizowane nagłówki: 5; cytowane w raporcie: 4." in md
    assert "Bank centralny pozostawia stopy bez zmian" not in md


# --- storage -------------------------------------------------------------------------


def _upgraded_db() -> Any:
    from market_intelligence.storage.db import create_db_engine, upgrade

    engine = create_db_engine(Settings().database_url)
    upgrade(engine)
    return engine


def _counts(engine: Any) -> dict[str, int]:
    from sqlalchemy import func, select

    from market_intelligence.storage.models import Base

    tables = [
        "runs",
        "instruments",
        "price_bars",
        "macro_observations",
        "news_items",
        "instrument_snapshots",
        "macro_snapshots",
        "reports",
        "deliveries",
        "run_errors",
    ]
    with engine.connect() as conn:
        return {
            t: conn.execute(select(func.count()).select_from(Base.metadata.tables[t])).scalar_one()
            for t in tables
        }


def test_mock_run_is_recorded_in_repository(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_intelligence.storage.sql import SqlRepository

    _smtp_env(monkeypatch)
    engine = _upgraded_db()
    sender = RecordingEmailSender()
    result = run_weekly(
        Settings(),
        mock=True,
        dry_run=False,
        email_sender=sender,
        repository=SqlRepository(engine),
    )
    assert result.report_ok
    assert result.storage_errors == []
    counts = _counts(engine)
    assert counts["runs"] == 1
    assert counts["instruments"] == 8
    assert counts["price_bars"] > 300
    assert counts["macro_observations"] > 100
    assert counts["news_items"] == 5
    assert counts["instrument_snapshots"] == 8
    assert counts["macro_snapshots"] == 4
    assert counts["reports"] == 1
    assert counts["deliveries"] == 1
    run = SqlRepository(engine).recent_runs()[0]
    assert run.id == result.run_id
    assert (run.status, run.mode, run.model) == ("ok", "mock", "mock")


def test_rerun_appends_history_without_duplicating_raw_data(isolated_env: Path) -> None:
    from market_intelligence.storage.sql import SqlRepository

    engine = _upgraded_db()
    for _ in range(2):
        run_weekly(Settings(), mock=True, dry_run=True, repository=SqlRepository(engine))
    counts = _counts(engine)
    assert counts["runs"] == 2
    assert counts["reports"] == 2
    assert counts["instrument_snapshots"] == 16
    first_bars = counts["price_bars"]
    run_weekly(Settings(), mock=True, dry_run=True, repository=SqlRepository(engine))
    assert _counts(engine)["price_bars"] == first_bars
    assert _counts(engine)["news_items"] == 5


def test_failed_report_and_errors_are_recorded(isolated_env: Path) -> None:
    from market_intelligence.storage.sql import SqlRepository

    engine = _upgraded_db()
    llm = FakeLLMClient(lambda s, u: "{not json")
    run_weekly(Settings(), mock=True, dry_run=True, llm=llm, repository=SqlRepository(engine))
    counts = _counts(engine)
    assert counts["reports"] == 1
    assert counts["run_errors"] >= 1
    assert SqlRepository(engine).recent_runs()[0].status == "failed"


def test_live_mode_requires_migrated_database(isolated_env: Path) -> None:
    with pytest.raises(ConfigError, match="mi db upgrade"):
        run_weekly(
            Settings(),
            mock=False,
            dry_run=True,
            price_provider=FixturePriceProvider(MOCK_PRICES_DIR),
            news_provider=FixtureNewsProvider(MOCK_RSS_DIR),
            llm=FakeLLMClient(lambda s, u: "{}"),
        )


class BrokenRepository:
    """Every write fails, like a database that went away mid-run."""

    def __getattr__(self, name: str) -> Any:
        from market_intelligence.storage.repository import StorageError

        def fail(*args: Any, **kwargs: Any) -> Any:
            raise StorageError("database error: OperationalError")

        return fail


def test_storage_failure_does_not_stop_report_or_email(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _smtp_env(monkeypatch)
    sender = RecordingEmailSender()
    result = run_weekly(
        Settings(), mock=True, dry_run=False, email_sender=sender, repository=BrokenRepository()
    )
    assert result.report_ok
    assert result.sent and len(sender.sent) == 1
    assert result.markdown_path.is_file()
    assert result.storage_errors
    assert result.storage_errors[0] == "start run: database error: OperationalError"
    assert result.run_id is None


def test_cli_db_upgrade_and_status(isolated_env: Path) -> None:
    runner = CliRunner()
    status = runner.invoke(app, ["db", "status"])
    assert status.exit_code == 0, status.output
    assert "not initialised" in status.output
    upgraded = runner.invoke(app, ["db", "upgrade"])
    assert upgraded.exit_code == 0, upgraded.output
    assert "revision 0001" in upgraded.output
    status = runner.invoke(app, ["db", "status"])
    assert "Schema:   0001 (code expects 0001)" in status.output


def test_cli_storage_error_exit_code(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from market_intelligence import cli, pipeline

    original = pipeline.run_weekly

    def broken_storage(settings: Settings, **kwargs: Any) -> Any:
        return original(settings, repository=BrokenRepository(), **kwargs)

    monkeypatch.setattr(cli, "run_weekly", broken_storage)
    result = CliRunner().invoke(app, ["weekly", "--mock", "--dry-run"])
    assert result.exit_code == 4
    assert "Storage error: start run" in result.output
