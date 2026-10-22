"""Application settings (env / .env) and YAML configuration loaders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import AliasChoices, BaseModel, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from market_intelligence.domain.models import Instrument, NewsSource


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
    news_lookback_days: int = Field(default=7, ge=1)
    news_max_items_per_source: int = Field(default=20, ge=1)
    news_max_items_for_llm: int = Field(default=40, ge=1)
    http_timeout_seconds: float = Field(default=15.0, gt=0)

    # --- Claude API ---
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "MI_ANTHROPIC_API_KEY")
    )
    llm_model: str = "claude-sonnet-5-5"
    llm_max_tokens: int = Field(default=16000, ge=1024)
    llm_timeout_seconds: float = Field(default=300.0, gt=0)
    llm_max_cost_usd: float = Field(default=0.50, gt=0)
    # DECISION: prices are configuration, not code. Defaults are the Claude Sonnet 5.5
    # base rates from https://platform.claude.com/docs/en/about-claude/pricing
    # (checked 2026-10-22). Update them when changing the model.
    llm_input_usd_per_mtok: float = Field(default=2.0, gt=0)
    llm_output_usd_per_mtok: float = Field(default=10.0, gt=0)
    # Server-side refusal fallback ("fallbacks": "default", beta header below).
    llm_refusal_fallback: bool = True

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
    instruments: list[Instrument]


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
