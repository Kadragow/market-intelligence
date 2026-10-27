"""Application settings (env / .env) and YAML configuration loaders."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from market_intelligence.data.http import RetryPolicy
from market_intelligence.domain.models import Instrument, MacroSeries, NewsSource


class ConfigError(Exception):
    """Raised when a configuration file is missing or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    config_dir: Path = Path("config")
    reports_dir: Path = Path("reports")

    # --- Data ---
    price_history_days: int = Field(default=90, ge=30)
    # DECISION: a series whose last bar is older than this many business days is STALE.
    stale_after_business_days: int = Field(default=5, ge=1)
    # DECISION: more than this many consecutive missing business days is reported as a gap
    # (single exchange holidays are normal).
    price_max_gap_business_days: int = Field(default=3, ge=1)
    news_lookback_days: int = Field(default=7, ge=1)
    news_max_items_per_source: int = Field(default=20, ge=1)
    news_max_items_for_llm: int = Field(default=40, ge=1)
    http_timeout_seconds: float = Field(default=15.0, gt=0)
    # Retries for data providers and the LLM API (exponential backoff with jitter).
    http_max_attempts: int = Field(default=3, ge=1, le=10)
    http_backoff_base_seconds: float = Field(default=1.0, ge=0)
    http_backoff_max_seconds: float = Field(default=30.0, ge=0)

    # --- Macro data (FRED) ---
    fred_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("FRED_API_KEY", "MI_FRED_API_KEY"),
    )
    # DECISION: about 18 months, so the observation from the same month a year before the
    # latest published one is included even when publication lags by several weeks.
    macro_history_days: int = Field(default=550, ge=60)

    # --- LLM via OpenRouter ---
    openrouter_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "MI_OPENROUTER_API_KEY"),
    )
    # OpenRouter model slug; free variants end in ":free".
    llm_model: str = "anthropic/claude-sonnet-5.5"
    llm_max_tokens: int = Field(default=16000, ge=1024)
    llm_timeout_seconds: float = Field(default=300.0, gt=0)
    llm_max_cost_usd: float = Field(default=0.50, gt=0)
    # DECISION: prices are configuration, not code, and OpenRouter enforces them as
    # provider.max_price. Defaults are the Anthropic first-party Claude Sonnet 5.5 rates
    # (USD 2 / 10 per million tokens, checked 2026-10-22); if OpenRouter charges more,
    # the request is not routed rather than overspending. Use 0 / 0 for ":free" models.
    llm_input_usd_per_mtok: float = Field(default=2.0, ge=0)
    llm_output_usd_per_mtok: float = Field(default=10.0, ge=0)
    # "allow" is OpenRouter's default; "deny" restricts to providers that do not store
    # prompts (fewer models, free ones may become unavailable).
    llm_data_collection: Literal["allow", "deny"] = "allow"

    # --- E-mail ---
    email_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_use_starttls: bool = True
    email_from: str | None = None
    # Comma-separated list of recipients.
    email_to: str = ""

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.http_max_attempts,
            base_delay_seconds=self.http_backoff_base_seconds,
            max_delay_seconds=self.http_backoff_max_seconds,
        )

    @property
    def email_recipients(self) -> list[str]:
        return [addr.strip() for addr in self.email_to.split(",") if addr.strip()]

    def smtp_missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.smtp_host:
            missing.append("MI_SMTP_HOST")
        if not self.smtp_username:
            missing.append("MI_SMTP_USERNAME")
        if self.smtp_password is None or not self.smtp_password.get_secret_value():
            missing.append("MI_SMTP_PASSWORD")
        if not self.email_from:
            missing.append("MI_EMAIL_FROM")
        if not self.email_recipients:
            missing.append("MI_EMAIL_TO")
        return missing


class _InstrumentsFile(BaseModel):
    instruments: list[Instrument] = Field(min_length=1)


class _MacroFile(BaseModel):
    series: list[MacroSeries] = Field(default_factory=list)


class _SourcesFile(BaseModel):
    feeds: list[NewsSource] | None = None


def _read_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_instruments(path: Path) -> list[Instrument]:
    try:
        parsed = _InstrumentsFile.model_validate(_read_yaml(path))
    except ValidationError as exc:
        raise ConfigError(f"Invalid instruments file {path}: {exc}") from exc
    ids = [i.id for i in parsed.instruments]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ConfigError(f"Duplicate instrument ids in {path}: {', '.join(duplicates)}")
    return parsed.instruments


def load_macro_series(path: Path) -> list[MacroSeries]:
    try:
        parsed = _MacroFile.model_validate(_read_yaml(path) or {})
    except ValidationError as exc:
        raise ConfigError(f"Invalid macro file {path}: {exc}") from exc
    ids = [s.id for s in parsed.series]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ConfigError(f"Duplicate macro series ids in {path}: {', '.join(duplicates)}")
    return parsed.series


def load_sources(path: Path) -> list[NewsSource]:
    try:
        parsed = _SourcesFile.model_validate(_read_yaml(path) or {})
    except ValidationError as exc:
        raise ConfigError(f"Invalid sources file {path}: {exc}") from exc
    feeds = parsed.feeds or []
    ids = [f.id for f in feeds]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ConfigError(f"Duplicate source ids in {path}: {', '.join(duplicates)}")
    return feeds
