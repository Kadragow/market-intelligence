import sys
import types
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import pytest

from market_intelligence.data.providers.yfinance_prices import YFinancePriceProvider
from market_intelligence.domain.models import AssetClass, Instrument

NOW = datetime(2026, 10, 18, 16, 0, tzinfo=UTC)
INSTRUMENT = Instrument(
    id="FX_USDPLN", name="USD/PLN", asset_class=AssetClass.FX, symbols={"yfinance": "PLN=X"}
)


def _install_fake_yfinance(monkeypatch: pytest.MonkeyPatch, history: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **kwargs: Any) -> Any:
            calls.append({"symbol": self.symbol, **kwargs})
            if isinstance(history, Exception):
                raise history
            return history

    module = types.ModuleType("yfinance")
    module.Ticker = FakeTicker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", module)
    return calls


def test_session_date_is_kept_when_exchange_tz_is_ahead_of_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = pd.DatetimeIndex(["2026-10-15", "2026-10-16"]).tz_localize("Europe/London")
    frame = pd.DataFrame(
        {
            "Open": [4.0, 4.1],
            "High": [4.2, 4.2],
            "Low": [3.9, 4.0],
            "Close": [4.1, float("nan")],
            "Adj Close": [4.1, float("nan")],
            "Volume": [0, 0],
        },
        index=index,
    )
    calls = _install_fake_yfinance(monkeypatch, frame)
    result = YFinancePriceProvider().get_daily_history(
        INSTRUMENT, date(2026, 10, 1), date(2026, 10, 16), NOW
    )
    assert calls[0]["symbol"] == "PLN=X"
    assert calls[0]["auto_adjust"] is False
    assert calls[0]["end"] == "2026-10-17"  # exclusive end
    assert [b.observed_at for b in result.bars] == [
        datetime(2026, 10, 15, tzinfo=UTC),
        datetime(2026, 10, 16, tzinfo=UTC),
    ]


def test_provider_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_yfinance(monkeypatch, RuntimeError("symbol not found"))
    result = YFinancePriceProvider().get_daily_history(
        INSTRUMENT, date(2026, 10, 1), date(2026, 10, 16), NOW
    )
    assert result.bars == []
    assert result.error is not None and "RuntimeError" in result.error


def test_empty_frame_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_yfinance(monkeypatch, pd.DataFrame())
    result = YFinancePriceProvider().get_daily_history(
        INSTRUMENT, date(2026, 10, 1), date(2026, 10, 16), NOW
    )
    assert result.error == "provider returned no rows"


def test_missing_symbol_for_provider() -> None:
    instrument = Instrument(id="X", name="X", asset_class=AssetClass.ETF)
    result = YFinancePriceProvider().get_daily_history(
        instrument, date(2026, 10, 1), date(2026, 10, 16), NOW
    )
    assert result.error == "no yfinance symbol configured"
