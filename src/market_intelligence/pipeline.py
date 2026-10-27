"""Weekly report pipeline: fetch -> compute -> model draft -> validate -> render -> save -> send."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from market_intelligence.ai.client import FakeLLMClient, LLMClient
from market_intelligence.ai.mock_writer import mock_draft
from market_intelligence.ai.schema import ReportInput
from market_intelligence.ai.writer import DraftResult, build_report_input, generate_draft
from market_intelligence.analysis.data_quality import select_news
from market_intelligence.analysis.macro_metrics import MacroSnapshot, compute_macro_snapshot
from market_intelligence.analysis.price_metrics import InstrumentSnapshot, compute_snapshot
from market_intelligence.analysis.rules import RULES_VERSION, config_hash
from market_intelligence.config import (
    ConfigError,
    Settings,
    load_instruments,
    load_macro_series,
    load_sources,
)
from market_intelligence.data.providers.base import (
    MacroProvider,
    MacroSeriesResult,
    NewsProvider,
    PriceProvider,
    clean_bars,
)
from market_intelligence.delivery.email import (
    EmailError,
    EmailSender,
    OutgoingEmail,
    SmtpEmailSender,
    decide_sending,
)
from market_intelligence.domain.models import DataProvenance, DataStatus
from market_intelligence.mock_data import (
    MOCK_FRED_DIR,
    MOCK_PRICES_DIR,
    MOCK_RSS_DIR,
    MOCK_SOURCES_FILE,
)
from market_intelligence.reports.render import (
    ReportContext,
    render_report,
    report_basename,
    save_report,
)
from market_intelligence.storage.db import (
    IN_MEMORY_URL,
    SchemaNotCurrentError,
    create_db_engine,
    ensure_schema_current,
    upgrade,
)
from market_intelligence.storage.recorder import RunRecorder
from market_intelligence.storage.repository import (
    DeliveryRecord,
    ReportRecord,
    Repository,
    RunFinish,
    RunStart,
)
from market_intelligence.storage.sql import SqlRepository

logger = logging.getLogger(__name__)

REPORT_TZ = ZoneInfo("Europe/Warsaw")
# DECISION: --mock runs on a frozen clock (Sunday 2026-10-18 18:00 Europe/Warsaw) so the
# output is deterministic and matches the synthetic data, whatever today's date is.
MOCK_NOW = datetime(2026, 10, 18, 16, 0, tzinfo=UTC)

UNAVAILABLE_DATA = [
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
    storage_errors: list[str] = field(default_factory=list)
    run_id: uuid.UUID | None = None


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
    macro_provider: MacroProvider | None = None,
    llm: LLMClient | None = None,
    email_sender: EmailSender | None = None,
    repository: Repository | None = None,
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

    def clock() -> datetime:
        return now if mock else datetime.now(UTC)

    recorder = _open_recorder(settings, mock, repository, clock)
    recorder.start(
        RunStart(
            report_week=report_week,
            mode="mock" if mock else "live",
            started_at=clock(),
            rules_version=RULES_VERSION,
            config_hash=config_hash(settings),
        )
    )
    recorder.instruments(instruments)

    start = (now - timedelta(days=settings.price_history_days)).date()
    snapshots: list[InstrumentSnapshot] = []
    for instrument in instruments:
        raw = prices.get_daily_history(instrument, start, now.date(), now)
        recorder.price_bars(clean_bars(raw.bars, instrument.asset_class)[0], prices.name, now)
        snap = compute_snapshot(
            instrument,
            raw,
            provenance=DataProvenance(
                provider=prices.name,
                dataset=prices.dataset,
                identifier=raw.symbol,
                fetched_at=now,
                attempts=raw.attempts,
            ),
            as_of=now.date(),
            stale_after_business_days=settings.stale_after_business_days,
            max_gap_business_days=settings.price_max_gap_business_days,
        )
        logger.info("Prices %s: %s (%d bars)", instrument.id, snap.status.value, snap.bar_count)
        snapshots.append(snap)

    macro = _fetch_macro(settings, mock, now, macro_provider, recorder)
    recorder.instrument_snapshots(snapshots, RULES_VERSION)
    recorder.macro_snapshots(macro, RULES_VERSION)

    news_result = news_src.fetch(sources, now)
    news, duplicates = select_news(
        news_result.items,
        now=now,
        lookback_days=settings.news_lookback_days,
        max_items=settings.news_max_items_for_llm,
    )
    recorder.news(news_result.items)
    for source_id, error in news_result.errors.items():
        logger.warning("News source %s failed: %s", source_id, error)
        recorder.error("news", f"{source_id}: {error}")
    logger.info(
        "News: %d items selected from %d sources (%d duplicates dropped)",
        len(news),
        len(sources),
        duplicates,
    )

    unavailable = list(UNAVAILABLE_DATA)
    if not sources:
        unavailable.append("wiadomości — brak skonfigurowanych kanałów RSS (config/sources.yaml)")

    report_input = build_report_input(
        snapshots=snapshots,
        news=news,
        macro=macro,
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
        macro=macro,
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
    for error in draft.errors:
        recorder.error("report", error)
    report_id = recorder.report(
        ReportRecord(
            report_week=report_week,
            kind="weekly",
            complete=draft.ok,
            subject=rendered.subject,
            markdown=rendered.markdown,
            html=rendered.html,
            draft=draft.draft.model_dump(mode="json") if draft.draft else None,
            errors=list(draft.errors),
            created_at=clock(),
        )
    )

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
            recorder.delivery(
                report_id,
                DeliveryRecord(
                    attempted_at=clock(),
                    status="sent",
                    recipients_count=len(settings.email_recipients),
                ),
            )
        except EmailError as exc:
            logger.error("E-mail delivery failed: %s", exc)
            result.email_error = str(exc)
            result.send_reason = "delivery failed"
            recorder.error("email", str(exc))
            recorder.delivery(
                report_id,
                DeliveryRecord(
                    attempted_at=clock(),
                    status="failed",
                    recipients_count=len(settings.email_recipients),
                    error=str(exc),
                ),
            )
    logger.info("Delivery: %s", result.send_reason)

    usage = draft.llm_response
    recorder.finish(
        RunFinish(
            status="ok" if draft.ok and result.email_error is None else "failed",
            finished_at=clock(),
            model=model_name,
            llm_input_tokens=usage.input_tokens if usage else None,
            llm_output_tokens=usage.output_tokens if usage else None,
            llm_cost=usage.cost_usd if usage else None,
        )
    )
    result.storage_errors = recorder.errors
    result.run_id = recorder.run_id
    return result


def _open_recorder(
    settings: Settings,
    mock: bool,
    repository: Repository | None,
    clock: Callable[[], datetime],
) -> RunRecorder:
    if repository is not None:
        return RunRecorder(repository, clock)
    if mock:
        # DECISION: --mock writes to an in-memory database: the storage code runs, the real
        # database stays free of synthetic data.
        engine = create_db_engine(IN_MEMORY_URL)
        upgrade(engine)
        return RunRecorder(SqlRepository(engine), clock)
    engine = create_db_engine(settings.database_url)
    try:
        ensure_schema_current(engine)
    except SchemaNotCurrentError as exc:
        # DECISION: an outdated schema is a setup problem; fail before fetching data.
        raise ConfigError(str(exc)) from exc
    except SQLAlchemyError as exc:
        recorder = RunRecorder(None, clock)
        recorder.fail("open database", f"database error: {type(exc).__name__}")
        return recorder
    return RunRecorder(SqlRepository(engine), clock)


def _fetch_macro(
    settings: Settings,
    mock: bool,
    now: datetime,
    provider: MacroProvider | None,
    recorder: RunRecorder | None = None,
) -> list[MacroSnapshot]:
    series_list = load_macro_series(settings.config_dir / "macro.yaml")
    if provider is None:
        provider = _default_macro_provider(settings, mock)
    # Start on the first day of the month so monthly observations are not cut off.
    start = (now - timedelta(days=settings.macro_history_days)).date().replace(day=1)
    snapshots: list[MacroSnapshot] = []
    for series in series_list:
        if provider is None:
            # DECISION: a missing FRED key does not stop the report; macro is marked as
            # insufficient data and no request is made.
            result = MacroSeriesResult([], error="brak klucza FRED_API_KEY")
            name, dataset = "fred", "not requested"
        else:
            result = provider.get_series(series, start, now.date(), now)
            name, dataset = provider.name, provider.dataset
            if recorder is not None:
                recorder.macro_observations(result.observations, name, now)
        snap = compute_macro_snapshot(
            series,
            result,
            provenance=DataProvenance(
                provider=name,
                dataset=dataset,
                identifier=series.id,
                fetched_at=now,
                attempts=result.attempts,
            ),
            as_of=now.date(),
        )
        logger.info("Macro %s: %s", series.id, snap.status.value)
        snapshots.append(snap)
    return snapshots


def _default_macro_provider(settings: Settings, mock: bool) -> MacroProvider | None:
    if mock:
        from market_intelligence.data.providers.fixtures import FixtureMacroProvider

        return FixtureMacroProvider(MOCK_FRED_DIR)
    key = settings.fred_api_key
    if key is None or not key.get_secret_value():
        logger.warning("FRED_API_KEY is not set; macro data will be marked as missing")
        return None
    from market_intelligence.data.providers.fred import FredMacroProvider

    return FredMacroProvider(
        api_key=key,
        timeout_seconds=settings.http_timeout_seconds,
        retry_policy=settings.retry_policy(),
    )


def _default_price_provider(settings: Settings, mock: bool) -> PriceProvider:
    if mock:
        from market_intelligence.data.providers.fixtures import FixturePriceProvider

        return FixturePriceProvider(MOCK_PRICES_DIR)
    from market_intelligence.data.providers.yfinance_prices import YFinancePriceProvider

    return YFinancePriceProvider(
        timeout_seconds=settings.http_timeout_seconds, retry_policy=settings.retry_policy()
    )


def _default_news_provider(settings: Settings, mock: bool) -> NewsProvider:
    if mock:
        from market_intelligence.data.providers.fixtures import FixtureNewsProvider

        return FixtureNewsProvider(MOCK_RSS_DIR, settings.news_max_items_per_source)
    from market_intelligence.data.providers.rss_news import RssNewsProvider

    return RssNewsProvider(
        timeout_seconds=settings.http_timeout_seconds,
        max_items_per_source=settings.news_max_items_per_source,
        retry_policy=settings.retry_policy(),
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
        retry_policy=settings.retry_policy(),
    )


def _require_api_key(settings: Settings) -> SecretStr:
    key = settings.openrouter_api_key
    if key is None or not key.get_secret_value():
        # DECISION: never fall back to the fake model silently outside --mock.
        raise ConfigError("OPENROUTER_API_KEY is not set. Use --mock to run without an LLM API.")
    return key
