"""Macro series from the FRED API (Federal Reserve Bank of St. Louis).

Based on the FRED API docs read 2026-10-27:
- GET https://api.stlouisfed.org/fred/series and /fred/series/observations,
  ``api_key`` and ``file_type=json`` as query parameters (no header auth is documented);
- observation values are strings; anything that is not a number is treated as missing;
- errors carry ``error_code`` / ``error_message``; up to 120 requests per minute.

The API key travels in the query string, so URLs and exception texts are never logged
or stored; only status codes and exception type names are.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, date, datetime
from typing import Any

import httpx
from pydantic import SecretStr

from market_intelligence.data.http import RetryPolicy, Sleep, request_with_retry
from market_intelligence.data.providers.base import MacroSeriesResult, clean_number
from market_intelligence.domain.models import MacroObservation, MacroSeries, MacroSeriesInfo

logger = logging.getLogger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred"
FRED_NOTICE = (
    "This product uses the FRED® API but is not endorsed or certified by the "
    "Federal Reserve Bank of St. Louis."
)
_SHORT_OFFSET_RE = re.compile(r"([+-]\d{2})$")
MAX_ERROR_CHARS = 150


class FredError(Exception):
    """A FRED request failed. The message never contains the API key."""


class FredMacroProvider:
    name = "fred"
    dataset = "fred/series/observations"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        timeout_seconds: float = 15.0,
        retry_policy: RetryPolicy | None = None,
        sleep: Sleep = time.sleep,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._policy = retry_policy or RetryPolicy()
        self._sleep = sleep

    def get_series(
        self, series: MacroSeries, start: date, end: date, fetched_at: datetime
    ) -> MacroSeriesResult:
        attempts = 0
        try:
            info_body, n = self._get("series", {"series_id": series.id})
            attempts += n
            obs_body, n = self._get(
                "series/observations",
                {
                    "series_id": series.id,
                    "observation_start": start.isoformat(),
                    "observation_end": end.isoformat(),
                    "sort_order": "asc",
                },
            )
            attempts += n
        except FredError as exc:
            logger.warning("FRED %s failed: %s", series.id, exc)
            return MacroSeriesResult([], error=str(exc), attempts=max(attempts, 1))
        return MacroSeriesResult(
            observations=parse_observations(series.id, obs_body),
            info=parse_series_info(info_body),
            attempts=attempts,
        )

    def _get(self, path: str, params: dict[str, str]) -> tuple[dict[str, Any], int]:
        query = {**params, "api_key": self._api_key.get_secret_value(), "file_type": "json"}
        try:
            result = request_with_retry(
                self._client,
                "GET",
                f"{FRED_BASE_URL}/{path}",
                policy=self._policy,
                sleep=self._sleep,
                params=query,
            )
        except httpx.HTTPError as exc:
            # str(exc) may contain the request URL with the API key.
            raise FredError(f"błąd połączenia z FRED: {type(exc).__name__}") from None
        response = result.response
        body = _json(response)
        if response.status_code != 200:
            message = body.get("error_message")
            detail = message if isinstance(message, str) else ""
            detail = detail.replace(self._api_key.get_secret_value(), "***")[:MAX_ERROR_CHARS]
            raise FredError(f"FRED HTTP {response.status_code}" + (f": {detail}" if detail else ""))
        return body, result.attempts


def parse_observations(series_id: str, body: dict[str, Any]) -> list[MacroObservation]:
    observations: list[MacroObservation] = []
    for row in body.get("observations") or []:
        if not isinstance(row, dict):
            continue
        try:
            day = date.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        observations.append(
            MacroObservation(
                series_id=series_id,
                observed_at=datetime(day.year, day.month, day.day, tzinfo=UTC),
                # Rates and indices may be zero or negative; non-numeric (".") is missing.
                value=clean_number(row.get("value"), allow_non_positive=True),
            )
        )
    return observations


def parse_series_info(body: dict[str, Any]) -> MacroSeriesInfo | None:
    rows = body.get("seriess")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    title = row.get("title")
    if not isinstance(title, str) or not title:
        return None
    return MacroSeriesInfo(
        title=title,
        units=_str_or_none(row.get("units")),
        frequency=_str_or_none(row.get("frequency")),
        updated_at=parse_last_updated(row.get("last_updated")),
    )


def parse_last_updated(value: object) -> datetime | None:
    """Parse FRED's ``2013-07-31 09:26:16-05`` format (offset without minutes)."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = _SHORT_OFFSET_RE.sub(r"\g<1>00", value.strip())
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S%z")
    except ValueError:
        return None
    return parsed.astimezone(UTC)


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
