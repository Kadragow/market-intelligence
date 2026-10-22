"""Daily prices from Yahoo Finance via yfinance.

Prototype only: yfinance is not a licensed, reliable production feed. Use it in
line with Yahoo's terms.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from market_intelligence.data.providers.base import PriceSeriesResult
from market_intelligence.domain.models import Instrument, PriceBar

logger = logging.getLogger(__name__)

_COLUMNS = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "adj_close": "Adj Close",
    "volume": "Volume",
}


class YFinancePriceProvider:
    name = "yfinance"

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self._timeout = timeout_seconds

    def get_daily_history(
        self, instrument: Instrument, start: date, end: date, fetched_at: datetime
    ) -> PriceSeriesResult:
        symbol = instrument.symbol_for(self.name)
        if symbol is None:
            return PriceSeriesResult([], None, error="no yfinance symbol configured")

        import yfinance as yf  # imported lazily: slow import, unused in --mock

        try:
            frame = yf.Ticker(symbol).history(
                start=start.isoformat(),
                # yfinance treats `end` as exclusive.
                end=(end + timedelta(days=1)).isoformat(),
                interval="1d",
                auto_adjust=False,
                actions=False,
                timeout=self._timeout,
                raise_errors=True,
            )
        except Exception as exc:  # yfinance raises a variety of exception types
            logger.warning("yfinance error for %s: %s", symbol, type(exc).__name__)
            return PriceSeriesResult([], symbol, error=f"{type(exc).__name__}: {exc}")

        if frame is None or frame.empty:
            return PriceSeriesResult([], symbol, error="provider returned no rows")

        bars = [_row_to_bar(instrument.id, index, row) for index, row in frame.iterrows()]
        return PriceSeriesResult(bars, symbol)


def _row_to_bar(instrument_id: str, index: Any, row: Any) -> PriceBar:
    timestamp = index.to_pydatetime()
    if timestamp.tzinfo is None:
        # DECISION: yfinance daily bars normally carry the exchange timezone; if not,
        # treat the session date as UTC midnight.
        timestamp = timestamp.replace(tzinfo=UTC)
    values = {field: row.get(column) for field, column in _COLUMNS.items()}
    return PriceBar(instrument_id=instrument_id, observed_at=timestamp, **values)
