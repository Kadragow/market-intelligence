"""Render the weekly report (Markdown + HTML, UTF-8) and save it to disk.

Tables, sources and timestamps are rendered by code from the data. The model's
draft supplies only the commentary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from market_intelligence.ai.schema import Claim, ReportDraft
from market_intelligence.ai.writer import price_decimals
from market_intelligence.analysis.price_metrics import InstrumentSnapshot
from market_intelligence.domain.models import DataStatus, NewsItem

DISCLAIMER = (
    "Materiał ma charakter badawczy i informacyjny. Nie stanowi rekomendacji "
    "inwestycyjnej ani porady finansowej. Wyniki historyczne nie gwarantują przyszłych."
)
MOCK_BANNER = (
    "DANE TESTOWE — raport wygenerowany w trybie --mock z syntetycznych danych. "
    "Nie są to prawdziwe notowania ani wiadomości."
)

STATUS_LABELS = {
    DataStatus.OK: "OK",
    DataStatus.STALE: "nieaktualne",
    DataStatus.INSUFFICIENT_DATA: "niewystarczające dane",
}

_TEMPLATES = Path(__file__).resolve().parent / "templates"


@dataclass
class SourceRef:
    number: int
    source_id: str
    label: str
    link: str | None
    details: str


@dataclass
class ReportContext:
    report_week: str
    as_of: datetime
    generated_at: datetime
    mock: bool
    model: str
    snapshots: list[InstrumentSnapshot]
    news: list[NewsItem]
    news_errors: dict[str, str]
    unavailable_data: list[str]
    draft: ReportDraft | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class RenderedReport:
    markdown: str
    html: str
    subject: str


def fmt_num(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "brak danych"
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "brak danych"
    return f"{value:+.2f}".replace(".", ",") + "%"


def fmt_dt(value: datetime | None) -> str:
    return "brak danych" if value is None else value.strftime("%Y-%m-%d %H:%M UTC")


def fmt_date(value: datetime | None) -> str:
    return "brak danych" if value is None else value.strftime("%Y-%m-%d")


_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|#])")


def md_escape(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATES),
        # HTML templates are escaped; Markdown output is escaped with the `md` filter.
        autoescape=select_autoescape(enabled_extensions=("html.j2",), default_for_string=False),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters.update(num=fmt_num, pct=fmt_pct, dt=fmt_dt, date=fmt_date, md=md_escape)
    return env


def _source_refs(ctx: ReportContext) -> list[SourceRef]:
    refs: list[SourceRef] = []
    for snap in ctx.snapshots:
        symbol = snap.symbol or "brak symbolu"
        refs.append(
            SourceRef(
                number=len(refs) + 1,
                source_id=snap.source_id,
                label=f"Ceny: {snap.instrument.name} ({snap.provider}, {symbol})",
                link=None,
                details=(
                    f"pobrano {fmt_dt(snap.fetched_at)}; "
                    f"ostatnia obserwacja {fmt_date(snap.last_observed_at)}"
                ),
            )
        )
    for item in ctx.news:
        refs.append(
            SourceRef(
                number=len(refs) + 1,
                source_id=item.id,
                label=f"{item.source_name}: {item.title}",
                link=item.link,
                details=(
                    f"opublikowano {fmt_dt(item.published_at)}; pobrano {fmt_dt(item.fetched_at)}"
                ),
            )
        )
    return refs


def render_report(ctx: ReportContext) -> RenderedReport:
    env = _environment()
    refs = _source_refs(ctx)
    numbers = {r.source_id: r.number for r in refs}

    def cite(claim: Claim) -> list[int]:
        return sorted({numbers[s] for s in claim.source_ids if s in numbers})

    data_issues = [
        (s.instrument.name, STATUS_LABELS[s.status], "; ".join(s.issues))
        for s in ctx.snapshots
        if s.status is not DataStatus.OK or s.issues
    ]
    template_ctx = {
        "ctx": ctx,
        "refs": refs,
        "cite": cite,
        "rows": [
            {
                "snap": s,
                "close": fmt_num(s.last_close, price_decimals(s.instrument.asset_class)),
                "status": STATUS_LABELS[s.status],
            }
            for s in ctx.snapshots
        ],
        "data_issues": data_issues,
        "disclaimer": DISCLAIMER,
        "mock_banner": MOCK_BANNER,
    }
    name = "weekly" if ctx.draft is not None else "failure"
    prefix = "[TEST] " if ctx.mock else ""
    if ctx.draft is not None:
        subject = f"{prefix}Tygodniowy brief rynkowy {ctx.report_week}"
    else:
        subject = f"{prefix}[NIEPOWODZENIE] Tygodniowy brief rynkowy {ctx.report_week}"
    return RenderedReport(
        markdown=env.get_template(f"{name}.md.j2").render(**template_ctx),
        html=env.get_template(f"{name}.html.j2").render(**template_ctx),
        subject=subject,
    )


def report_basename(report_week: str, *, mock: bool, failed: bool) -> str:
    name = f"weekly-{report_week}"
    if mock:
        name += "-mock"
    if failed:
        name += "-FAILED"
    return name


def save_report(report: RenderedReport, directory: Path, basename: str) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    md_path = directory / f"{basename}.md"
    html_path = directory / f"{basename}.html"
    md_path.write_text(report.markdown, encoding="utf-8", newline="\n")
    html_path.write_text(report.html, encoding="utf-8", newline="\n")
    return md_path, html_path
