from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from market_intelligence.analysis.macro_metrics import compute_macro_snapshot
from market_intelligence.config import load_macro_series
from market_intelligence.data.providers.base import MacroSeriesResult
from market_intelligence.data.providers.fixtures import FixtureMacroProvider
from market_intelligence.data.providers.fred import (
    FredMacroProvider,
    parse_last_updated,
    parse_observations,
)
from market_intelligence.domain.models import (
    DataProvenance,
    DataStatus,
    MacroFrequency,
    MacroObservation,
    MacroSeries,
    MacroSeriesInfo,
)
from market_intelligence.mock_data import MOCK_FRED_DIR

ROOT = Path(__file__).resolve().parents[2]

KEY = "abcdefghijklmnopqrstuvwxyz123456"
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
CPI = MacroSeries(
    id="CPIAUCSL", name="CPI", frequency=MacroFrequency.MONTHLY, stale_after_days=75, yoy=True
)
PROV = DataProvenance(provider="test", dataset="test", identifier="CPIAUCSL", fetched_at=NOW)

SERIES_BODY = {
    "seriess": [
        {
            "id": "CPIAUCSL",
            "title": "Consumer Price Index",
            "units": "Index 1982-1984=100",
            "frequency": "Monthly",
            "last_updated": "2026-10-15 07:44:02-05",
        }
    ]
}
OBS_BODY = {
    "observations": [
        {"date": "2026-08-01", "value": "100.5"},
        {"date": "2026-09-01", "value": "."},
        {"date": "bad", "value": "1"},
    ]
}


# --- parsing --------------------------------------------------------------------


def test_missing_value_dot_is_none_not_zero() -> None:
    obs = parse_observations("CPIAUCSL", OBS_BODY)
    assert [(o.observed_at.date(), o.value) for o in obs] == [
        (date(2026, 8, 1), 100.5),
        (date(2026, 9, 1), None),
    ]


def test_parse_last_updated_short_offset() -> None:
    assert parse_last_updated("2026-10-15 07:44:02-05") == datetime(
        2026, 10, 15, 12, 44, 2, tzinfo=UTC
    )
    assert parse_last_updated("nonsense") is None
    assert parse_last_updated(None) is None


def test_repository_macro_config() -> None:
    series = load_macro_series(ROOT / "config" / "macro.yaml")
    assert [s.id for s in series] == ["CPIAUCSL", "FEDFUNDS", "DGS10", "UNRATE"]


# --- FRED client (mock transport) -----------------------------------------------


class Fred:
    def __init__(self, *responses: tuple[int, dict[str, Any]] | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.responses.pop(0)
        if isinstance(step, Exception):
            raise step
        return httpx.Response(step[0], json=step[1])


def _provider(fred: Fred) -> FredMacroProvider:
    return FredMacroProvider(
        api_key=SecretStr(KEY),
        client=httpx.Client(transport=httpx.MockTransport(fred)),
        sleep=lambda seconds: None,
    )


def _fetch(fred: Fred) -> MacroSeriesResult:
    return _provider(fred).get_series(CPI, date(2025, 9, 1), date(2026, 10, 27), NOW)


def test_fred_success_and_request_params() -> None:
    fred = Fred((200, SERIES_BODY), (200, OBS_BODY))
    result = _fetch(fred)
    assert result.error is None
    assert result.info is not None and result.info.units == "Index 1982-1984=100"
    assert len(result.observations) == 2
    assert result.attempts == 1  # two requests, none retried
    series_req, obs_req = fred.requests
    assert series_req.url.path == "/fred/series"
    assert obs_req.url.path == "/fred/series/observations"
    assert obs_req.url.params["series_id"] == "CPIAUCSL"
    assert obs_req.url.params["file_type"] == "json"
    assert obs_req.url.params["observation_start"] == "2025-09-01"
    assert obs_req.url.params["api_key"] == KEY


def test_fred_error_message_without_key() -> None:
    body = {"error_code": 400, "error_message": f"Bad Request. The value {KEY} is not registered."}
    result = _fetch(Fred((400, body)))
    assert result.observations == []
    assert result.error is not None
    assert result.error.startswith("FRED HTTP 400: Bad Request.")
    assert KEY not in result.error


def test_fred_connection_error_hides_url(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    exc = httpx.ConnectError(f"failed https://api.stlouisfed.org/fred/series?api_key={KEY}")
    result = _fetch(Fred(exc, exc, exc))
    assert result.error == "błąd połączenia z FRED: ConnectError"
    assert KEY not in caplog.text


def test_fred_rate_limit_is_retried() -> None:
    fred = Fred((429, {}), (200, SERIES_BODY), (200, OBS_BODY))
    result = _fetch(fred)
    assert result.error is None
    assert result.attempts == 2  # the series request was retried once


# --- macro snapshot ----------------------------------------------------------------


def _obs(day: date, value: float | None) -> MacroObservation:
    return MacroObservation(
        series_id="CPIAUCSL",
        observed_at=datetime(day.year, day.month, day.day, tzinfo=UTC),
        value=value,
    )


def _monthly(last: date, count: int, start: float = 100.0) -> list[MacroObservation]:
    out = []
    year, month = last.year, last.month
    for i in range(count):
        out.append(_obs(date(year, month, 1), start + count - 1 - i))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return list(reversed(out))


def _snapshot(observations: list[MacroObservation], series: MacroSeries = CPI) -> Any:
    result = MacroSeriesResult(
        observations, info=MacroSeriesInfo(title="CPI", units="Index", frequency="Monthly")
    )
    return compute_macro_snapshot(series, result, provenance=PROV, as_of=date(2026, 10, 27))


def test_macro_snapshot_values_and_yoy() -> None:
    snap = _snapshot(_monthly(date(2026, 9, 1), 13))
    assert snap.status is DataStatus.OK
    assert snap.last_value == 112.0
    assert snap.previous_value == 111.0
    assert snap.change == pytest.approx(1.0)
    assert snap.yoy_change_pct == pytest.approx((112 / 100 - 1) * 100)
    assert snap.last_period == datetime(2026, 9, 1, tzinfo=UTC)


def test_macro_missing_value_is_skipped_and_reported() -> None:
    observations = [*_monthly(date(2026, 8, 1), 3), _obs(date(2026, 9, 1), None)]
    snap = _snapshot(observations)
    assert snap.last_value == 102.0
    assert snap.last_period == datetime(2026, 8, 1, tzinfo=UTC)
    assert "brakujące wartości w serii: 1" in snap.issues


def test_macro_stale_series() -> None:
    snap = _snapshot(_monthly(date(2026, 6, 1), 13))
    assert snap.status is DataStatus.STALE
    assert any("dni" in i for i in snap.issues)


def test_macro_without_history_has_no_yoy() -> None:
    snap = _snapshot(_monthly(date(2026, 9, 1), 5))
    assert snap.yoy_change_pct is None
    assert any("r/r" in i for i in snap.issues)


def test_macro_no_data_is_insufficient() -> None:
    result = MacroSeriesResult([], error="FRED HTTP 500")
    snap = compute_macro_snapshot(CPI, result, provenance=PROV, as_of=date(2026, 10, 27))
    assert snap.status is DataStatus.INSUFFICIENT_DATA
    assert snap.last_value is None
    assert snap.issues == ["FRED HTTP 500"]


def test_macro_duplicates_are_dropped() -> None:
    observations = [*_monthly(date(2026, 9, 1), 3), _obs(date(2026, 9, 1), 102.0)]
    snap = _snapshot(observations)
    assert any("zduplikowane" in i for i in snap.issues)


def test_fixture_macro_provider_reads_mock_data() -> None:
    series = load_macro_series(ROOT / "config" / "macro.yaml")
    provider = FixtureMacroProvider(MOCK_FRED_DIR)
    results = {
        s.id: provider.get_series(s, date(2025, 9, 1), date(2026, 10, 18), NOW) for s in series
    }
    assert all(r.error is None for r in results.values())
    assert any(o.value is None for o in results["DGS10"].observations)
    info = results["CPIAUCSL"].info
    assert info is not None and info.title.startswith("[TEST]")


def test_daily_series_holidays_are_not_reported() -> None:
    daily = MacroSeries(id="DGS10", name="10Y", frequency=MacroFrequency.DAILY, stale_after_days=7)
    observations = [_obs(date(2026, 10, 22), 4.9), _obs(date(2026, 10, 23), None)]
    snap = _snapshot(observations, daily)
    assert snap.issues == []
    assert snap.last_value == 4.9
