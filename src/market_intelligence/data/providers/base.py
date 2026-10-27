"""Provider interfaces and shared data-cleaning helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from market_intelligence.domain.models import (
    AssetClass,
    Instrument,
    NewsItem,
    NewsSource,
    PriceBar,
)


class PriceProvider(Protocol):
    name: str

    def get_daily_history(
        self, instrument: Instrument, start: date, end: date, fetched_at: datetime
    ) -> PriceSeriesResult: ...


class NewsProvider(Protocol):
    name: str

    def fetch(self, sources: list[NewsSource], fetched_at: datetime) -> NewsFetchResult: ...


@dataclass
class PriceSeriesResult:
    """Raw provider output before staleness is evaluated."""

    bars: list[PriceBar]
    symbol: str | None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class NewsFetchResult:
    items: list[NewsItem]
    # source_id -> error message
    errors: dict[str, str] = field(default_factory=dict)


def clean_number(value: object, *, allow_non_positive: bool = False) -> float | None:
    """Return a finite float or ``None``. Never substitutes zero for missing data."""
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, int | float | str):
        try:
            value = float(value)  # type: ignore[arg-type]  # numpy scalars
        except (TypeError, ValueError):
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if not allow_non_positive and number <= 0:
        return None
    return number


def clean_bars(bars: list[PriceBar], asset_class: AssetClass) -> tuple[list[PriceBar], list[str]]:
    """Sort, de-duplicate and sanity-check bars. Returns (bars, warnings)."""
    warnings: list[str] = []
    by_time: dict[datetime, PriceBar] = {}
    for bar in bars:
        if bar.observed_at in by_time:
            warnings.append(f"zduplikowana świeca z {bar.observed_at.date().isoformat()} usunięta")
        by_time[bar.observed_at] = bar

    # Yields may legitimately be zero or negative; prices may not.
    allow_non_positive = asset_class is AssetClass.YIELD
    cleaned: list[PriceBar] = []
    for observed_at in sorted(by_time):
        bar = by_time[observed_at]
        fixed = bar.model_copy(
            update={
                name: clean_number(getattr(bar, name), allow_non_positive=allow_non_positive)
                for name in ("open", "high", "low", "close", "adj_close")
            }
            | {"volume": _clean_volume(bar.volume, asset_class)}
        )
        if fixed.close is None and bar.close is not None:
            warnings.append(
                f"nieprawidłowe zamknięcie z {observed_at.date().isoformat()} oznaczone jako brak"
            )
        cleaned.append(fixed)
    return cleaned, warnings


def _clean_volume(value: float | None, asset_class: AssetClass) -> float | None:
    # DECISION: Yahoo reports volume 0 for FX and yields, meaning "not available".
    # Treat it as missing, never as a real zero.
    if asset_class in (AssetClass.FX, AssetClass.YIELD):
        return None
    return clean_number(value, allow_non_positive=False)
