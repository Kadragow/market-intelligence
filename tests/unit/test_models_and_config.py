from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from market_intelligence.config import ConfigError, Settings, load_instruments, load_sources
from market_intelligence.domain.models import AssetClass, PriceBar

ROOT = Path(__file__).resolve().parents[2]


def test_price_bar_normalises_to_utc() -> None:
    warsaw = timezone(timedelta(hours=2))
    bar = PriceBar(instrument_id="X", observed_at=datetime(2026, 10, 16, 2, 0, tzinfo=warsaw))
    assert bar.observed_at == datetime(2026, 10, 16, 0, 0, tzinfo=UTC)
    assert bar.observed_at.tzinfo is UTC


def test_price_bar_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        PriceBar(instrument_id="X", observed_at=datetime(2026, 10, 16))  # noqa: DTZ001


def test_price_bar_missing_values_are_none() -> None:
    bar = PriceBar(instrument_id="X", observed_at=datetime(2026, 10, 16, tzinfo=UTC))
    assert bar.close is None
    assert bar.volume is None


def test_repository_instruments_file_is_valid() -> None:
    instruments = load_instruments(ROOT / "config" / "instruments.yaml")
    ids = [i.id for i in instruments]
    assert ids == [
        "US_SPY",
        "US_QQQ",
        "DE_EWG",
        "JP_EWJ",
        "GOLD_GLD",
        "FX_EURUSD",
        "FX_USDPLN",
        "US_10Y",
    ]
    assert instruments[-1].asset_class is AssetClass.YIELD
    assert instruments[0].symbol_for("yfinance") == "SPY"


def test_repository_sources_file_is_valid_and_empty() -> None:
    assert load_sources(ROOT / "config" / "sources.yaml") == []


def test_duplicate_instrument_ids_rejected(tmp_path: Path) -> None:
    path = tmp_path / "instruments.yaml"
    path.write_text(
        "instruments:\n"
        "  - {id: A, name: A, asset_class: etf}\n"
        "  - {id: A, name: B, asset_class: etf}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="Duplicate"):
        load_instruments(path)


def test_missing_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_instruments(tmp_path / "nope.yaml")


def test_settings_defaults_without_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings()
    assert settings.llm_model == "claude-sonnet-5-5"
    assert settings.anthropic_api_key is None
    assert settings.email_enabled is False
    assert "MI_SMTP_HOST" in settings.smtp_missing_fields()


def test_settings_secret_not_in_repr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret-value")
    monkeypatch.setenv("MI_SMTP_PASSWORD", "smtp-secret-value")
    settings = Settings()
    assert settings.anthropic_api_key is not None
    assert "sk-test-secret-value" not in repr(settings)
    assert "smtp-secret-value" not in str(settings)


def test_email_recipients_parsing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MI_EMAIL_TO", " a@example.com , ,b@example.com")
    assert Settings().email_recipients == ["a@example.com", "b@example.com"]
