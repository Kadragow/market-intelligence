"""Turn code-computed data into model input, call the model, validate the draft."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from market_intelligence.ai.client import LLMClient, LLMError, LLMResponse
from market_intelligence.ai.prompts import SYSTEM_PROMPT, build_user_message
from market_intelligence.ai.schema import (
    InstrumentInput,
    MacroInput,
    NewsInput,
    ReportDraft,
    ReportInput,
    news_source_id,
    report_draft_schema,
)
from market_intelligence.ai.validation import validate_draft
from market_intelligence.analysis.macro_metrics import MacroSnapshot
from market_intelligence.analysis.price_metrics import InstrumentSnapshot
from market_intelligence.domain.models import AssetClass, NewsItem

logger = logging.getLogger(__name__)


@dataclass
class DraftResult:
    draft: ReportDraft | None
    errors: list[str] = field(default_factory=list)
    llm_response: LLMResponse | None = None

    @property
    def ok(self) -> bool:
        return self.draft is not None and not self.errors


def build_report_input(
    *,
    snapshots: list[InstrumentSnapshot],
    news: list[NewsItem],
    macro: list[MacroSnapshot] | None = None,
    report_week: str,
    as_of_date: str,
    unavailable_data: list[str],
) -> ReportInput:
    return ReportInput(
        report_week=report_week,
        as_of_date=as_of_date,
        instruments=[_instrument_input(s) for s in snapshots],
        macro=[_macro_input(m) for m in macro or []],
        news=[
            NewsInput(
                source_id=news_source_id(i),
                title=n.title,
                source_name=n.source_name,
                category=n.category,
                published_date=n.published_at.date().isoformat() if n.published_at else None,
                lead=n.summary,
            )
            for i, n in enumerate(news)
        ],
        unavailable_data=unavailable_data,
    )


def price_decimals(asset_class: AssetClass) -> int:
    return 4 if asset_class is AssetClass.FX else 2


def macro_decimals(units: str | None) -> int:
    # DECISION: rates in percent are shown with 2 decimals, indices and levels with 3.
    return 2 if units and units.lower().startswith("percent") else 3


def _macro_input(snap: MacroSnapshot) -> MacroInput:
    decimals = macro_decimals(snap.units)
    return MacroInput(
        source_id=snap.source_id,
        series_id=snap.series.id,
        name=snap.series.name,
        title=snap.title,
        units=snap.units,
        frequency=snap.series.frequency.value,
        data_status=snap.status.value,
        last_value=_round(snap.last_value, decimals),
        last_period=snap.last_period.date().isoformat() if snap.last_period else None,
        previous_value=_round(snap.previous_value, decimals),
        change=_round(snap.change, decimals),
        yoy_change_pct=_round(snap.yoy_change_pct, 2),
        data_issues=list(snap.issues),
    )


def _round(value: float | None, decimals: int) -> float | None:
    return None if value is None else round(value, decimals)


def _instrument_input(snap: InstrumentSnapshot) -> InstrumentInput:
    instrument = snap.instrument
    return InstrumentInput(
        source_id=snap.source_id,
        instrument_id=instrument.id,
        name=instrument.name,
        asset_class=instrument.asset_class.value,
        currency=instrument.currency,
        data_status=snap.status.value,
        last_close=_round(snap.last_close, price_decimals(instrument.asset_class)),
        weekly_change_pct=_round(snap.change_1w_pct, 2),
        monthly_change_pct=_round(snap.change_1m_pct, 2),
        last_observation_date=(
            snap.last_observed_at.date().isoformat() if snap.last_observed_at else None
        ),
        notes=instrument.data_notes,
        data_issues=list(snap.issues),
    )


def generate_draft(llm: LLMClient, report_input: ReportInput) -> DraftResult:
    try:
        response = llm.generate(
            system=SYSTEM_PROMPT,
            user=build_user_message(report_input),
            output_model=ReportDraft,
            json_schema=report_draft_schema(sorted(report_input.source_ids())),
        )
    except LLMError as exc:
        logger.error("LLM call failed: %s", exc)
        return DraftResult(None, [f"llm: {exc}"])

    outcome = validate_draft(response.text, report_input)
    if not outcome.ok:
        logger.error("Model output rejected: %d validation errors", len(outcome.errors))
        for err in outcome.errors:
            logger.error("  %s", err)
        return DraftResult(None, outcome.errors, response)
    return DraftResult(outcome.draft, [], response)
