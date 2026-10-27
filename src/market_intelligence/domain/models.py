"""Minimal domain models for Stage 1.

All timestamps are timezone-aware and normalised to UTC. A missing value is
``None``, never zero.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]


class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"
    FX = "fx"
    YIELD = "yield"
    COMMODITY = "commodity"
    CRYPTO = "crypto"


class DataStatus(StrEnum):
    OK = "ok"
    STALE = "stale"
    INSUFFICIENT_DATA = "insufficient_data"


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    asset_class: AssetClass
    exchange: str | None = None
    currency: str | None = None
    # Symbol per provider, e.g. {"yfinance": "SPY"}.
    symbols: dict[str, str] = Field(default_factory=dict)
    data_notes: str | None = None

    def symbol_for(self, provider: str) -> str | None:
        return self.symbols.get(provider)


class DataProvenance(BaseModel):
    """Where a piece of data came from and when it was fetched."""

    model_config = ConfigDict(frozen=True)

    provider: str
    # Endpoint or dataset within the provider, without credentials.
    dataset: str
    # Provider-side identifier: ticker symbol, series id, feed URL.
    identifier: str | None
    fetched_at: UtcDatetime
    attempts: int = Field(default=1, ge=1)


class PriceBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument_id: str
    # Start of the trading session the bar describes.
    observed_at: UtcDatetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    # Close adjusted for dividends and splits, when the provider supplies it.
    adj_close: float | None = None
    volume: float | None = None


class PriceSeries(BaseModel):
    """Daily bars for one instrument from one provider, with provenance."""

    instrument_id: str
    provider: str
    symbol: str | None
    fetched_at: UtcDatetime
    status: DataStatus
    bars: list[PriceBar] = Field(default_factory=list)
    error: str | None = None


class NewsSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    category: str = "general"


class NewsItem(BaseModel):
    """Headline metadata only. Full article text is never stored."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    link: str
    source_id: str
    source_name: str
    category: str
    published_at: UtcDatetime | None
    fetched_at: UtcDatetime
    summary: str | None = None

    @staticmethod
    def make_id(link: str) -> str:
        return "news-" + hashlib.sha256(link.encode("utf-8")).hexdigest()[:12]


class MacroFrequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class MacroSeries(BaseModel):
    """A configured macro series (config/macro.yaml)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    frequency: MacroFrequency
    stale_after_days: int = Field(ge=1)
    yoy: bool = False


class MacroObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    series_id: str
    # Start of the period the value describes (e.g. 2026-09-01 for September).
    observed_at: UtcDatetime
    value: float | None


class MacroSeriesInfo(BaseModel):
    """Series metadata as reported by the provider."""

    model_config = ConfigDict(frozen=True)

    title: str
    units: str | None = None
    frequency: str | None = None
    # When the provider last updated the series (its publication time).
    updated_at: UtcDatetime | None = None
