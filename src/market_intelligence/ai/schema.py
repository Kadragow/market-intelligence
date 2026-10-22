"""Input given to the model and the structured output expected back.

The model only interprets data computed by code. Every statement it makes
carries source ids from the input; numbers are checked in ``validation.py``.
"""

from __future__ import annotations

from typing import Literal

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
    news: list[NewsInput]
    unavailable_data: list[str]

    def source_ids(self) -> set[str]:
        return {i.source_id for i in self.instruments} | {n.source_id for n in self.news}


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
