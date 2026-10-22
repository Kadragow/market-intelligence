"""Weekly report pipeline: fetch -> compute -> model draft -> validate -> render -> save -> send."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from market_intelligence.ai.client import FakeLLMClient, LLMClient
from market_intelligence.ai.mock_writer import mock_draft
from market_intelligence.ai.schema import ReportInput
from market_intelligence.ai.writer import DraftResult, build_report_input, generate_draft
from market_intelligence.analysis.price_metrics import InstrumentSnapshot, compute_snapshot
from market_intelligence.config import ConfigError, Settings, load_instruments, load_sources
from market_intelligence.data.providers.base import NewsProvider, PriceProvider
from market_intelligence.delivery.email import (
    EmailError,
    EmailSender,
    OutgoingEmail,
    SmtpEmailSender,
    decide_sending,
)
from market_intelligence.domain.models import DataStatus, NewsItem
from market_intelligence.mock_data import MOCK_PRICES_DIR, MOCK_RSS_DIR, MOCK_SOURCES_FILE
from market_intelligence.reports.render import (
    ReportContext,
    render_report,
    report_basename,
    save_report,
)

logger = logging.getLogger(__name__)

REPORT_TZ = ZoneInfo("Europe/Warsaw")
# DECISION: --mock runs on a frozen clock (Sunday 2026-10-18 18:00 Europe/Warsaw) so the
# output is deterministic and matches the synthetic data, whatever today's date is.
MOCK_NOW = datetime(2026, 10, 18, 16, 0, tzinfo=UTC)

UNAVAILABLE_IN_STAGE_1 = [
    "dane makroekonomiczne (FRED) — planowane w Etapie 2",
    "dane fundamentalne spółek — planowane w Etapie 4",
    "kalendarz wydarzeń",
]


@dataclass
class WeeklyRunResult:
    report_ok: bool
    markdown_path: Path
    html_path: Path
    sent: bool
    send_reason: str
    errors: list[str] = field(default_factory=list)
    email_error: str | None = None


def iso_week(moment: datetime) -> str:
    year, week, _ = moment.astimezone(REPORT_TZ).isocalendar()
    return f"{year}-W{week:02d}"


def run_weekly(
    settings: Settings,
    *,
    mock: bool,
    dry_run: bool,
    now: datetime | None = None,
    price_provider: PriceProvider | None = None,
    news_provider: NewsProvider | None = None,
    llm: LLMClient | None = None,
    email_sender: EmailSender | None = None,
) -> WeeklyRunResult:
    now = now or (MOCK_NOW if mock else datetime.now(UTC))
    report_week = iso_week(now)
    logger.info("Weekly run: week=%s mock=%s dry_run=%s", report_week, mock, dry_run)

    instruments = load_instruments(settings.config_dir / "instruments.yaml")
    sources = load_sources(MOCK_SOURCES_FILE if mock else settings.config_dir / "sources.yaml")
    prices = price_provider or _default_price_provider(settings, mock)
    news_src = news_provider or _default_news_provider(settings, mock)
    if not mock and llm is None:
        _require_api_key(settings)  # fail before spending time on data fetching

    start = (now - timedelta(days=settings.price_history_days)).date()
    snapshots: list[InstrumentSnapshot] = []
    for instrument in instruments:
        raw = prices.get_daily_history(instrument, start, now.date(), now)
        snap = compute_snapshot(
            instrument,
            raw,
            provider=prices.name,
            fetched_at=now,
            as_of=now.date(),
            stale_after_business_days=settings.stale_after_business_days,
        )
        logger.info("Prices %s: %s (%d bars)", instrument.id, snap.status.value, snap.bar_count)
        snapshots.append(snap)

    news_result = news_src.fetch(sources, now)
    news = _select_news(news_result.items, now, settings)
    for source_id, error in news_result.errors.items():
        logger.warning("News source %s failed: %s", source_id, error)
    logger.info("News: %d items selected from %d sources", len(news), len(sources))

    unavailable = list(UNAVAILABLE_IN_STAGE_1)
    if not sources:
        unavailable.append("wiadomości — brak skonfigurowanych kanałów RSS (config/sources.yaml)")

    report_input = build_report_input(
        snapshots=snapshots,
        news=news,
        report_week=report_week,
        as_of_date=now.date().isoformat(),
        unavailable_data=unavailable,
    )

    model_name = "mock" if mock else settings.llm_model
    if not any(s.status is DataStatus.OK for s in snapshots):
        # DECISION: without any usable price data there is nothing to interpret; skip the
        # (paid) model call and produce a failure report.
        draft = DraftResult(None, ["no instrument has sufficient, fresh price data"])
    else:
        client = llm or _default_llm(settings, mock, report_input)
        draft = generate_draft(client, report_input)
        if draft.llm_response is not None:
            model_name = draft.llm_response.model

    ctx = ReportContext(
        report_week=report_week,
        as_of=now,
        generated_at=now if mock else datetime.now(UTC),
        mock=mock,
        model=model_name,
        snapshots=snapshots,
        news=news,
        news_errors=news_result.errors,
        unavailable_data=unavailable,
        draft=draft.draft,
        errors=draft.errors,
    )
    rendered = render_report(ctx)
    md_path, html_path = save_report(
        rendered,
        settings.reports_dir,
        report_basename(report_week, mock=mock, failed=not draft.ok),
    )
    logger.info("Report saved: %s, %s", md_path, html_path)

    result = WeeklyRunResult(
        report_ok=draft.ok,
        markdown_path=md_path,
        html_path=html_path,
        sent=False,
        send_reason="",
        errors=draft.errors,
    )
    decision = decide_sending(settings, dry_run=dry_run)
    if not draft.ok:
        result.send_reason = "report incomplete; not sent"
    elif not decision.send:
        result.send_reason = decision.reason
    else:
        sender = email_sender or SmtpEmailSender(settings)
        email_from = settings.email_from or ""
        try:
            sender.send(
                OutgoingEmail(
                    subject=rendered.subject,
                    text=rendered.markdown,
                    html=rendered.html,
                    sender=email_from,
                    recipients=settings.email_recipients,
                )
            )
            result.sent = True
            result.send_reason = "sent"
        except EmailError as exc:
            logger.error("E-mail delivery failed: %s", exc)
            result.email_error = str(exc)
            result.send_reason = "delivery failed"
    logger.info("Delivery: %s", result.send_reason)
    return result


def _select_news(items: list[NewsItem], now: datetime, settings: Settings) -> list[NewsItem]:
    cutoff = now - timedelta(days=settings.news_lookback_days)
    unique: dict[str, NewsItem] = {}
    for item in items:
        # DECISION: items without a publication date are kept (shown as "brak danych");
        # items published after the as-of time are dropped.
        if item.published_at is not None and not cutoff <= item.published_at <= now:
            continue
        unique.setdefault(item.id, item)
    oldest = datetime.min.replace(tzinfo=UTC)
    ordered = sorted(unique.values(), key=lambda n: n.published_at or oldest, reverse=True)
    return ordered[: settings.news_max_items_for_llm]


def _default_price_provider(settings: Settings, mock: bool) -> PriceProvider:
    if mock:
        from market_intelligence.data.providers.fixtures import FixturePriceProvider

        return FixturePriceProvider(MOCK_PRICES_DIR)
    from market_intelligence.data.providers.yfinance_prices import YFinancePriceProvider

    return YFinancePriceProvider(timeout_seconds=settings.http_timeout_seconds)


def _default_news_provider(settings: Settings, mock: bool) -> NewsProvider:
    if mock:
        from market_intelligence.data.providers.fixtures import FixtureNewsProvider

        return FixtureNewsProvider(MOCK_RSS_DIR, settings.news_max_items_per_source)
    from market_intelligence.data.providers.rss_news import RssNewsProvider

    return RssNewsProvider(
        timeout_seconds=settings.http_timeout_seconds,
        max_items_per_source=settings.news_max_items_per_source,
    )


def _default_llm(settings: Settings, mock: bool, report_input: ReportInput) -> LLMClient:
    if mock:
        draft_json = mock_draft(report_input).model_dump_json()
        return FakeLLMClient(lambda system, user: draft_json, model="mock")
    api_key = _require_api_key(settings)
    from market_intelligence.ai.openrouter_client import OpenRouterLLMClient

    return OpenRouterLLMClient(
        api_key=api_key,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        max_cost_usd=settings.llm_max_cost_usd,
        input_usd_per_mtok=settings.llm_input_usd_per_mtok,
        output_usd_per_mtok=settings.llm_output_usd_per_mtok,
        timeout_seconds=settings.llm_timeout_seconds,
        data_collection=settings.llm_data_collection,
    )


def _require_api_key(settings: Settings) -> SecretStr:
    key = settings.openrouter_api_key
    if key is None or not key.get_secret_value():
        # DECISION: never fall back to the fake model silently outside --mock.
        raise ConfigError("OPENROUTER_API_KEY is not set. Use --mock to run without an LLM API.")
    return key
