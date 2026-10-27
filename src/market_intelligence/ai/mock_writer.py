"""Deterministic stand-in for the model, used by ``--mock``.

It writes simple Polish sentences using only values from the input, so the
draft passes the same validation as a real model response.
"""

from __future__ import annotations

from market_intelligence.ai.schema import (
    Claim,
    InstrumentInput,
    MacroInput,
    ReportDraft,
    ReportInput,
    ResearchCandidate,
    Scenarios,
)


def _pct(value: float) -> str:
    return f"{value:+.2f}".replace(".", ",") + "%"


def _describe(i: InstrumentInput) -> str:
    parts = []
    if i.weekly_change_pct is not None:
        parts.append(f"tygodniowa zmiana {_pct(i.weekly_change_pct)}")
    if i.monthly_change_pct is not None:
        parts.append(f"miesięczna zmiana {_pct(i.monthly_change_pct)}")
    return f"{i.name}: " + (", ".join(parts) if parts else "brak wyliczonych zmian") + "."


def _fmt(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _describe_macro(m: MacroInput) -> str:
    if m.last_value is None:
        return f"{m.name}: brak aktualnej wartości."
    text = f"{m.name}: ostatnia wartość {_fmt(m.last_value)} ({m.last_period})"
    if m.yoy_change_pct is not None:
        text += f", zmiana rok do roku {_pct(m.yoy_change_pct)}"
    return text + "."


def mock_draft(report_input: ReportInput) -> ReportDraft:
    ok = [i for i in report_input.instruments if i.data_status == "ok"]
    bad = [i for i in report_input.instruments if i.data_status != "ok"]
    ranked = sorted(
        (i for i in ok if i.weekly_change_pct is not None),
        key=lambda i: i.weekly_change_pct or 0.0,
        reverse=True,
    )
    tech_news = [n for n in report_input.news if n.category in ("technology", "ai")]
    other_news = [n for n in report_input.news if n not in tech_news]

    summary = [
        Claim(
            text=(
                "Tryb testowy: raport oparty na syntetycznych danych. "
                + (_describe(ranked[0]) if ranked else "Brak instrumentów z kompletnymi danymi.")
            ),
            source_ids=[ranked[0].source_id] if ranked else [report_input.instruments[0].source_id],
        )
    ]
    markets = [Claim(text=_describe(i), source_ids=[i.source_id]) for i in ok]
    macro = [
        Claim(text=_describe_macro(m), source_ids=[m.source_id])
        for m in report_input.macro
        if m.data_status == "ok"
    ]
    warnings = [
        Claim(
            text=f"{i.name}: dane niekompletne lub nieaktualne, instrument pominięto w ocenie.",
            source_ids=[i.source_id],
        )
        for i in bad
    ] + [
        Claim(
            text=f"{m.name}: dane makro niekompletne lub nieaktualne.",
            source_ids=[m.source_id],
        )
        for m in report_input.macro
        if m.data_status != "ok"
    ]
    sectors = [
        Claim(text=f"Nagłówek ({n.source_name}): {n.title}", source_ids=[n.source_id])
        for n in other_news[:2]
    ]
    technology = [
        Claim(text=f"Nagłówek ({n.source_name}): {n.title}", source_ids=[n.source_id])
        for n in tech_news[:3]
    ]

    candidates = []
    if ranked:
        top = ranked[0]
        candidates.append(
            ResearchCandidate(
                instrument_id=top.instrument_id,
                horizon="krótkoterminowy",
                thesis=Claim(
                    text=f"Najsilniejsza tygodniowa zmiana w zestawieniu. {_describe(top)}",
                    source_ids=[top.source_id],
                ),
                counterarguments=[
                    Claim(
                        text="Krótki okres obserwacji; ruch może być przypadkowy.",
                        source_ids=[top.source_id],
                    )
                ],
                risks=["Odwrócenie krótkoterminowego trendu."],
                uncertainties=["Dane syntetyczne nie odzwierciedlają rynku."],
                invalidation_conditions=["Spadek poniżej poziomu sprzed tygodnia."],
            )
        )

    anchor = (ranked[0] if ranked else report_input.instruments[0]).source_id
    return ReportDraft(
        summary=summary,
        global_markets=markets,
        macro=macro,
        sectors_and_companies=sectors,
        research_candidates=candidates,
        warning_signs=warnings,
        technology=technology,
        scenarios=Scenarios(
            positive=Claim(text="Kontynuacja obecnych wzrostów.", source_ids=[anchor]),
            base=Claim(text="Ruch boczny przy zmiennych nastrojach.", source_ids=[anchor]),
            negative=Claim(text="Odwrócenie ostatnich zmian.", source_ids=[anchor]),
        ),
        limitations=[
            "Dane w tym raporcie są syntetyczne i służą wyłącznie do testów.",
            "Brak danych fundamentalnych w tym etapie.",
        ],
    )
