"""Daily prices from Yahoo Finance via yfinance.

Prototype only: yfinance is not a licensed, reliable production feed. Use it in
line with Yahoo's terms.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from market_intelligence.data.http import RetryPolicy, Sleep, call_with_retry
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
    dataset = "Ticker.history(interval=1d, auto_adjust=False)"

    def __init__(
        self,
        timeout_seconds: float = 15.0,
        retry_policy: RetryPolicy | None = None,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._timeout = timeout_seconds
        self._policy = retry_policy or RetryPolicy()
        self._sleep = sleep

    def get_daily_history(
        self, instrument: Instrument, start: date, end: date, fetched_at: datetime
    ) -> PriceSeriesResult:
        symbol = instrument.symbol_for(self.name)
        if symbol is None:
            return PriceSeriesResult([], None, error="brak symbolu yfinance w konfiguracji")

        import yfinance as yf  # imported lazily: slow import, unused in --mock

        def fetch() -> Any:
            return yf.Ticker(symbol).history(
                start=start.isoformat(),
                # yfinance treats `end` as exclusive.
                end=(end + timedelta(days=1)).isoformat(),
                interval="1d",
                auto_adjust=False,
                actions=False,
                timeout=self._timeout,
                raise_errors=True,
            )

        try:
            # DECISION: every yfinance exception is retried; it raises many unrelated types,
            # so an unknown symbol also costs the configured attempts.
            frame, attempts = call_with_retry(
                fetch, policy=self._policy, description=f"yfinance {symbol}", sleep=self._sleep
            )
        except Exception as exc:  # yfinance raises a variety of exception types
            logger.warning("yfinance error for %s: %s", symbol, type(exc).__name__)
            return PriceSeriesResult(
                [],
                symbol,
                error=f"błąd dostawcy: {type(exc).__name__}",
                attempts=self._policy.max_attempts,
            )

        if frame is None or frame.empty:
            return PriceSeriesResult(
                [], symbol, error="dostawca nie zwrócił danych", attempts=attempts
            )

        bars = [_row_to_bar(instrument.id, index, row) for index, row in frame.iterrows()]
        return PriceSeriesResult(bars, symbol, attempts=attempts)


def _row_to_bar(instrument_id: str, index: Any, row: Any) -> PriceBar:
    # DECISION: daily bars are stamped at local midnight of the exchange timezone.
    # Converting that instant to UTC can shift the date (e.g. London, Tokyo), so we keep
    # the session date and store it as 00:00 UTC.
    session_date = index.to_pydatetime().date()
    observed_at = datetime(session_date.year, session_date.month, session_date.day, tzinfo=UTC)
    values = {field: row.get(column) for field, column in _COLUMNS.items()}
    return PriceBar(instrument_id=instrument_id, observed_at=observed_at, **values)
