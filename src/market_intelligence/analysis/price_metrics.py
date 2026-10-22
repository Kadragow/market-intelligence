"""Simple, transparent price metrics per instrument. No scoring, no signals."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

import numpy as np
from pydantic import BaseModel

from market_intelligence.data.providers.base import PriceSeriesResult, clean_bars
from market_intelligence.domain.models import DataStatus, Instrument, PriceBar, UtcDatetime

ReturnBasis = Literal["adj_close", "close"]

# A reference bar may be at most this many calendar days before the target date
# (weekends, holidays). Otherwise the change is reported as missing.
_MAX_REFERENCE_GAP_DAYS = 4


class InstrumentSnapshot(BaseModel):
    instrument: Instrument
    source_id: str
    provider: str
    symbol: str | None
    fetched_at: UtcDatetime
    status: DataStatus
    last_close: float | None = None
    last_observed_at: UtcDatetime | None = None
    change_1w_pct: float | None = None
    change_1m_pct: float | None = None
    return_basis: ReturnBasis | None = None
    bar_count: int = 0
    issues: list[str] = []


def source_id_for_prices(instrument_id: str) -> str:
    return f"price-{instrument_id}"


def compute_snapshot(
    instrument: Instrument,
    result: PriceSeriesResult,
    *,
    provider: str,
    fetched_at: datetime,
    as_of: date,
    stale_after_business_days: int,
) -> InstrumentSnapshot:
    issues = list(result.warnings)
    if result.error:
        issues.append(result.error)
    bars, clean_warnings = clean_bars(result.bars, instrument.asset_class)
    issues.extend(clean_warnings)
    valid = [b for b in bars if b.close is not None]

    common = {
        "instrument": instrument,
        "source_id": source_id_for_prices(instrument.id),
        "provider": provider,
        "symbol": result.symbol,
        "fetched_at": fetched_at,
        "bar_count": len(valid),
    }
    if not valid:
        if not issues:
            issues.append("brak prawidłowych cen zamknięcia")
        return InstrumentSnapshot(status=DataStatus.INSUFFICIENT_DATA, issues=issues, **common)

    last = valid[-1]
    last_date = last.observed_at.date()
    lag = int(np.busday_count(last_date, as_of))
    status = DataStatus.OK
    if lag > stale_after_business_days:
        status = DataStatus.STALE
        issues.append(f"ostatnia obserwacja sprzed {lag} dni roboczych")

    week_ref = _reference_bar(valid, last_date - timedelta(days=7))
    month_ref = _reference_bar(valid, last_date - timedelta(days=30))
    week_change, week_basis = _pct_change(week_ref, last)
    month_change, month_basis = _pct_change(month_ref, last)
    if month_ref is None:
        issues.append("za krótka historia do wyliczenia zmiany miesięcznej")

    return InstrumentSnapshot(
        status=status,
        last_close=last.close,
        last_observed_at=last.observed_at,
        change_1w_pct=week_change,
        change_1m_pct=month_change,
        return_basis=week_basis or month_basis,
        issues=issues,
        **common,
    )


def _reference_bar(bars: list[PriceBar], target: date) -> PriceBar | None:
    candidates = [b for b in bars if b.observed_at.date() <= target]
    if not candidates:
        return None
    ref = candidates[-1]
    if (target - ref.observed_at.date()).days > _MAX_REFERENCE_GAP_DAYS:
        return None
    return ref


def _pct_change(ref: PriceBar | None, last: PriceBar) -> tuple[float | None, ReturnBasis | None]:
    if ref is None:
        return None, None
    # DECISION: use adjusted closes when both bars have them (dividends/splits),
    # otherwise raw closes. The basis is reported alongside the value.
    if ref.adj_close is not None and last.adj_close is not None:
        return (last.adj_close / ref.adj_close - 1) * 100, "adj_close"
    if ref.close is not None and last.close is not None and ref.close != 0:
        return (last.close / ref.close - 1) * 100, "close"
    return None, None
