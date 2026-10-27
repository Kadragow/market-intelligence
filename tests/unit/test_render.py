from datetime import UTC, datetime

from market_intelligence.ai.schema import Claim, ReportDraft, Scenarios
from market_intelligence.analysis.price_metrics import InstrumentSnapshot
from market_intelligence.domain.models import AssetClass, DataProvenance, DataStatus, Instrument
from market_intelligence.reports.render import (
    DISCLAIMER,
    ReportContext,
    fmt_num,
    fmt_pct,
    md_escape,
    render_report,
    report_basename,
)

NOW = datetime(2026, 10, 18, 16, 0, tzinfo=UTC)


def _ctx(text: str = "Tekst <script>alert(1)</script> | x") -> ReportContext:
    snap = InstrumentSnapshot(
        instrument=Instrument(id="US_SPY", name="S&P 500", asset_class=AssetClass.ETF),
        source_id="price-US_SPY",
        provenance=DataProvenance(
            provider="test", dataset="test", identifier="SPY", fetched_at=NOW
        ),
        status=DataStatus.OK,
        last_close=1234.5,
        last_observed_at=NOW,
        change_1w_pct=-1.234,
    )
    claim = Claim(text=text, source_ids=["price-US_SPY"])
    draft = ReportDraft(
        summary=[claim],
        global_markets=[],
        macro=[],
        sectors_and_companies=[],
        research_candidates=[],
        warning_signs=[],
        technology=[],
        scenarios=Scenarios(positive=claim, base=claim, negative=claim),
        limitations=["Ograniczenie."],
    )
    return ReportContext(
        report_week="2026-W42",
        as_of=NOW,
        generated_at=NOW,
        mock=False,
        model="anthropic/claude-sonnet-5.5",
        snapshots=[snap],
        news=[],
        news_errors={},
        unavailable_data=[],
        draft=draft,
    )


def test_polish_number_formatting() -> None:
    assert fmt_num(1234.5) == "1 234,50"
    assert fmt_num(None) == "brak danych"
    assert fmt_pct(-1.234) == "-1,23%"
    assert fmt_pct(None) == "brak danych"


def test_html_escapes_model_text() -> None:
    html = render_report(_ctx()).html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_markdown_escapes_model_text() -> None:
    md = render_report(_ctx()).markdown
    assert "<script>" not in md
    assert md_escape("a|b") == "a\\|b"


def test_complete_report_has_footer_and_no_mock_banner() -> None:
    rendered = render_report(_ctx("Zwykły tekst."))
    assert DISCLAIMER in rendered.markdown
    assert DISCLAIMER in rendered.html
    assert "DANE TESTOWE" not in rendered.markdown
    assert rendered.subject == "Tygodniowy brief rynkowy 2026-W42"
    assert "1 234,50" in rendered.markdown


def test_failure_report() -> None:
    ctx = _ctx()
    ctx.draft = None
    ctx.errors = ["llm: OpenRouter error: HTTP 429 (rate limited)"]
    rendered = render_report(ctx)
    assert rendered.subject.startswith("[NIEPOWODZENIE]")
    assert "HTTP 429" in rendered.markdown
    assert DISCLAIMER in rendered.html


def test_report_basename() -> None:
    assert report_basename("2026-W42", mock=False, failed=False) == "weekly-2026-W42"
    assert report_basename("2026-W42", mock=True, failed=True) == "weekly-2026-W42-mock-FAILED"
