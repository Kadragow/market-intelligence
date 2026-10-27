"""Offline providers that read local fixture files (used by --mock and tests)."""

from __future__ import annotations

import csv
import json
from datetime import UTC, date, datetime
from pathlib import Path

from market_intelligence.data.providers.base import (
    MacroSeriesResult,
    NewsFetchResult,
    PriceSeriesResult,
)
from market_intelligence.data.providers.fred import parse_observations, parse_series_info
from market_intelligence.data.providers.rss_news import newest_first, parse_feed
from market_intelligence.domain.models import Instrument, MacroSeries, NewsSource, PriceBar


class FixturePriceProvider:
    """Reads ``<directory>/<instrument_id>.csv`` with columns
    date,open,high,low,close,adj_close,volume (empty cell = missing)."""

    name = "fixture"
    dataset = "synthetic daily CSV"

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def get_daily_history(
        self, instrument: Instrument, start: date, end: date, fetched_at: datetime
    ) -> PriceSeriesResult:
        symbol = instrument.symbol_for("yfinance")
        path = self._directory / f"{instrument.id}.csv"
        if not path.is_file():
            return PriceSeriesResult([], symbol, error="brak danych testowych dla instrumentu")

        bars: list[PriceBar] = []
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                day = date.fromisoformat(row["date"])
                if not start <= day <= end:
                    continue
                bars.append(
                    PriceBar(
                        instrument_id=instrument.id,
                        observed_at=datetime(day.year, day.month, day.day, tzinfo=UTC),
                        **{k: (row.get(k) or None) for k in _VALUE_COLUMNS},
                    )
                )
        if not bars:
            return PriceSeriesResult([], symbol, error="brak danych testowych w zadanym okresie")
        return PriceSeriesResult(bars, symbol)


_VALUE_COLUMNS = ("open", "high", "low", "close", "adj_close", "volume")


class FixtureNewsProvider:
    """Reads ``<directory>/<source_id>.xml`` RSS files instead of fetching URLs."""

    name = "fixture"

    def __init__(self, directory: Path, max_items_per_source: int = 20) -> None:
        self._directory = directory
        self._max_items = max_items_per_source

    def fetch(self, sources: list[NewsSource], fetched_at: datetime) -> NewsFetchResult:
        result = NewsFetchResult(items=[])
        for source in sources:
            path = self._directory / f"{source.id}.xml"
            if not path.is_file():
                result.errors[source.id] = "brak testowego kanału dla źródła"
                continue
            items = newest_first(parse_feed(path.read_bytes(), source, fetched_at))
            result.items.extend(items[: self._max_items])
        return result


class FixtureMacroProvider:
    """Reads ``<directory>/<series_id>.json`` holding FRED-shaped responses:
    ``{"series": <fred/series body>, "observations": <fred/series/observations body>}``."""

    name = "fixture"
    dataset = "synthetic FRED-shaped JSON"

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def get_series(
        self, series: MacroSeries, start: date, end: date, fetched_at: datetime
    ) -> MacroSeriesResult:
        path = self._directory / f"{series.id}.json"
        if not path.is_file():
            return MacroSeriesResult([], error="brak danych testowych dla serii")
        body = json.loads(path.read_text(encoding="utf-8"))
        observations = [
            o
            for o in parse_observations(series.id, body.get("observations") or {})
            if start <= o.observed_at.date() <= end
        ]
        return MacroSeriesResult(observations, info=parse_series_info(body.get("series") or {}))
