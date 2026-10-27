"""Input given to the model and the structured output expected back.

The model only interprets data computed by code. Every statement it makes
carries source ids from the input; numbers are checked in ``validation.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Model input (computed by code) ------------------------------------------


class InstrumentInput(BaseModel):
    source_id: str
    instrument_id: str
    name: str
    asset_class: str
    currency: str | None
    data_status: Literal["ok", "stale", "insufficient_data"]
    last_close: float | None
    weekly_change_pct: float | None
    monthly_change_pct: float | None
    last_observation_date: str | None
    notes: str | None = None
    data_issues: list[str] = []


class MacroInput(BaseModel):
    source_id: str
    series_id: str
    name: str
    title: str | None
    units: str | None
    frequency: str
    data_status: Literal["ok", "stale", "insufficient_data"]
    last_value: float | None
    last_period: str | None
    previous_value: float | None
    # last_value - previous_value in the series' units (percentage points for rates).
    change: float | None
    yoy_change_pct: float | None
    data_issues: list[str] = []


class NewsInput(BaseModel):
    source_id: str
    title: str
    source_name: str
    category: str
    published_date: str | None
    lead: str | None


class ReportInput(BaseModel):
    report_week: str
    as_of_date: str
    instruments: list[InstrumentInput]
    macro: list[MacroInput] = []
    news: list[NewsInput]
    unavailable_data: list[str]

    def source_ids(self) -> set[str]:
        return (
            {i.source_id for i in self.instruments}
            | {m.source_id for m in self.macro}
            | {n.source_id for n in self.news}
        )


# --- Model output ---------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Claim(_Strict):
    text: str = Field(description="1-3 zdania po polsku.")
    source_ids: list[str] = Field(
        min_length=1, description="Identyfikatory źródeł z danych wejściowych (source_id)."
    )


class ResearchCandidate(_Strict):
    instrument_id: str = Field(description="instrument_id z danych wejściowych.")
    horizon: Literal["krótkoterminowy", "długoterminowy"]
    thesis: Claim
    counterarguments: list[Claim] = Field(min_length=1)
    risks: list[str] = Field(min_length=1)
    uncertainties: list[str] = Field(min_length=1)
    invalidation_conditions: list[str] = Field(min_length=1)


class Scenarios(_Strict):
    positive: Claim
    base: Claim
    negative: Claim


class ReportDraft(_Strict):
    summary: list[Claim] = Field(min_length=1)
    global_markets: list[Claim]
    macro: list[Claim]
    sectors_and_companies: list[Claim]
    research_candidates: list[ResearchCandidate]
    warning_signs: list[Claim]
    technology: list[Claim]
    scenarios: Scenarios
    limitations: list[str] = Field(min_length=1)


def news_source_id(index: int) -> str:
    """Short per-report id for the news item at ``index`` (0-based) in the model input.

    DECISION: models copy short ids reliably but tend to mangle long hash-like ones
    (observed: an invented "news-00123aefg654"), so the model sees news-01, news-02, ...
    The stable NewsItem.id is kept for storage.
    """
    return f"news-{index + 1:02d}"


def report_draft_schema(allowed_source_ids: list[str]) -> dict[str, Any]:
    """JSON schema of ReportDraft with source ids restricted to the given values.

    Providers that enforce strict structured outputs then cannot return unknown ids;
    code-side validation still checks them for providers that treat the schema as a hint.
    """
    schema = ReportDraft.model_json_schema()
    claim = schema["$defs"]["Claim"]["properties"]["source_ids"]
    claim["items"] = {"type": "string", "enum": sorted(allowed_source_ids)}
    return schema
