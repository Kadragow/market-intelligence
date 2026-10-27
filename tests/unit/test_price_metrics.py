from datetime import UTC, date, datetime, timedelta

import pytest

from market_intelligence.analysis.price_metrics import compute_snapshot
from market_intelligence.data.providers.base import PriceSeriesResult
from market_intelligence.domain.models import (
    AssetClass,
    DataProvenance,
    DataStatus,
    Instrument,
    PriceBar,
)

NOW = datetime(2026, 10, 18, 16, 0, tzinfo=UTC)
AS_OF = date(2026, 10, 18)
INSTRUMENT = Instrument(id="US_SPY", name="SPY", asset_class=AssetClass.ETF)


def _series(last_day: date, days: int, start_price: float = 100.0) -> list[PriceBar]:
    bars = []
    day = last_day - timedelta(days=days)
    price = start_price
    while day <= last_day:
        if day.weekday() < 5:
            bars.append(
                PriceBar(
                    instrument_id="US_SPY",
                    observed_at=datetime(day.year, day.month, day.day, tzinfo=UTC),
                    close=price,
                    adj_close=price,
                )
            )
            price += 1.0
        day += timedelta(days=1)
    return bars


def test_ok_snapshot_with_changes() -> None:
    bars = _series(date(2026, 10, 16), 45)
    snap = compute_snapshot(
        INSTRUMENT,
        PriceSeriesResult(bars, "SPY"),
        provenance=DataProvenance(
            provider="test", dataset="test", identifier="SPY", fetched_at=NOW
        ),
        as_of=AS_OF,
        stale_after_business_days=5,
    )
    assert snap.status is DataStatus.OK
    assert snap.last_close == bars[-1].close
    assert snap.last_observed_at == datetime(2026, 10, 16, tzinfo=UTC)
    # 1W reference: 2026-10-09 (5 sessions earlier, price +5)
    week_ref = next(b for b in bars if b.observed_at.date() == date(2026, 10, 9))
    assert week_ref.close is not None and bars[-1].close is not None
    assert snap.change_1w_pct == pytest.approx((bars[-1].close / week_ref.close - 1) * 100)
    assert snap.change_1m_pct is not None
    assert snap.return_basis == "adj_close"


def test_no_bars_is_insufficient_data_not_zero() -> None:
    snap = compute_snapshot(
        INSTRUMENT,
        PriceSeriesResult([], "SPY", error="dostawca nie zwrócił danych"),
        provenance=DataProvenance(
            provider="test", dataset="test", identifier="SPY", fetched_at=NOW
        ),
        as_of=AS_OF,
        stale_after_business_days=5,
    )
    assert snap.status is DataStatus.INSUFFICIENT_DATA
    assert snap.last_close is None
    assert snap.change_1w_pct is None
    assert "dostawca nie zwrócił danych" in snap.issues


def test_stale_series_is_flagged() -> None:
    bars = _series(date(2026, 10, 2), 45)
    snap = compute_snapshot(
        INSTRUMENT,
        PriceSeriesResult(bars, "SPY"),
        provenance=DataProvenance(
            provider="test", dataset="test", identifier="SPY", fetched_at=NOW
        ),
        as_of=AS_OF,
        stale_after_business_days=5,
    )
    assert snap.status is DataStatus.STALE
    assert any("dni roboczych" in i for i in snap.issues)


def test_short_history_has_no_month_change() -> None:
    bars = _series(date(2026, 10, 16), 10)
    snap = compute_snapshot(
        INSTRUMENT,
        PriceSeriesResult(bars, "SPY"),
        provenance=DataProvenance(
            provider="test", dataset="test", identifier="SPY", fetched_at=NOW
        ),
        as_of=AS_OF,
        stale_after_business_days=5,
    )
    assert snap.change_1w_pct is not None
    assert snap.change_1m_pct is None
    assert "za krótka historia do wyliczenia zmiany miesięcznej" in snap.issues


def test_all_closes_missing_is_insufficient() -> None:
    bars = [PriceBar(instrument_id="US_SPY", observed_at=datetime(2026, 10, 16, tzinfo=UTC))]
    snap = compute_snapshot(
        INSTRUMENT,
        PriceSeriesResult(bars, "SPY"),
        provenance=DataProvenance(
            provider="test", dataset="test", identifier="SPY", fetched_at=NOW
        ),
        as_of=AS_OF,
        stale_after_business_days=5,
    )
    assert snap.status is DataStatus.INSUFFICIENT_DATA
    assert snap.issues == ["brak prawidłowych cen zamknięcia"]


def test_raw_close_used_when_adjusted_missing() -> None:
    bars = [b.model_copy(update={"adj_close": None}) for b in _series(date(2026, 10, 16), 45)]
    snap = compute_snapshot(
        INSTRUMENT,
        PriceSeriesResult(bars, "SPY"),
        provenance=DataProvenance(
            provider="test", dataset="test", identifier="SPY", fetched_at=NOW
        ),
        as_of=AS_OF,
        stale_after_business_days=5,
    )
    assert snap.return_basis == "close"
