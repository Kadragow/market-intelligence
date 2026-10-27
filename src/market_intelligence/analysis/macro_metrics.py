"""Macro series snapshots: latest value, previous value, change, year-over-year."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from market_intelligence.data.providers.base import MacroSeriesResult
from market_intelligence.domain.models import (
    DataProvenance,
    DataStatus,
    MacroFrequency,
    MacroObservation,
    MacroSeries,
    UtcDatetime,
)


class MacroSnapshot(BaseModel):
    series: MacroSeries
    source_id: str
    provenance: DataProvenance
    status: DataStatus
    title: str | None = None
    units: str | None = None
    updated_at: UtcDatetime | None = None
    last_value: float | None = None
    last_period: UtcDatetime | None = None
    previous_value: float | None = None
    # last_value - previous_value, in the series' own units (e.g. percentage points).
    change: float | None = None
    yoy_change_pct: float | None = None
    issues: list[str] = []


def source_id_for_macro(series_id: str) -> str:
    return f"macro-{series_id}"


def compute_macro_snapshot(
    series: MacroSeries,
    result: MacroSeriesResult,
    *,
    provenance: DataProvenance,
    as_of: date,
) -> MacroSnapshot:
    issues: list[str] = []
    if result.error:
        issues.append(result.error)
    observations, duplicates = _dedupe(result.observations)
    if duplicates:
        issues.append(f"usunięto zduplikowane obserwacje: {duplicates}")
    missing = sum(1 for o in observations if o.value is None)
    # DECISION: daily series (e.g. DGS10) mark market holidays as missing; that is expected,
    # so missing values are reported only for lower-frequency series.
    if missing and series.frequency is not MacroFrequency.DAILY:
        issues.append(f"brakujące wartości w serii: {missing}")
    valid = [o for o in observations if o.value is not None]

    info = result.info
    common = {
        "series": series,
        "source_id": source_id_for_macro(series.id),
        "provenance": provenance,
        "title": info.title if info else None,
        "units": info.units if info else None,
        "updated_at": info.updated_at if info else None,
    }
    if not valid:
        if not issues:
            issues.append("brak obserwacji")
        return MacroSnapshot(status=DataStatus.INSUFFICIENT_DATA, issues=issues, **common)

    last = valid[-1]
    previous = valid[-2] if len(valid) > 1 else None
    age_days = (as_of - last.observed_at.date()).days
    status = DataStatus.OK
    if age_days > series.stale_after_days:
        status = DataStatus.STALE
        issues.append(f"ostatnia obserwacja sprzed {age_days} dni")

    yoy: float | None = None
    if series.yoy:
        yoy = _yoy_change(valid, last)
        if yoy is None:
            issues.append("brak obserwacji sprzed roku do wyliczenia zmiany r/r")

    return MacroSnapshot(
        status=status,
        last_value=last.value,
        last_period=last.observed_at,
        previous_value=previous.value if previous else None,
        change=(last.value - previous.value)
        if previous and last.value is not None and previous.value is not None
        else None,
        yoy_change_pct=yoy,
        issues=issues,
        **common,
    )


def _dedupe(observations: list[MacroObservation]) -> tuple[list[MacroObservation], int]:
    by_period: dict[date, MacroObservation] = {}
    for obs in observations:
        by_period[obs.observed_at.date()] = obs
    ordered = [by_period[d] for d in sorted(by_period)]
    return ordered, len(observations) - len(ordered)


def _yoy_change(valid: list[MacroObservation], last: MacroObservation) -> float | None:
    period = last.observed_at.date()
    # DECISION: year-over-year uses the observation for exactly the same period one
    # year earlier (same month and day); no interpolation.
    try:
        target = period.replace(year=period.year - 1)
    except ValueError:  # 29 February
        return None
    base = next((o for o in valid if o.observed_at.date() == target), None)
    if base is None or base.value is None or base.value == 0 or last.value is None:
        return None
    return (last.value / base.value - 1) * 100
