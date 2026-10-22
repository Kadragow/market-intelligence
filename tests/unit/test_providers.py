from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from market_intelligence.config import load_sources
from market_intelligence.data.providers.base import clean_bars, clean_number
from market_intelligence.data.providers.fixtures import FixtureNewsProvider, FixturePriceProvider
from market_intelligence.data.providers.rss_news import RssNewsProvider, parse_feed
from market_intelligence.domain.models import AssetClass, Instrument, NewsSource, PriceBar
from market_intelligence.mock_data import MOCK_PRICES_DIR, MOCK_RSS_DIR, MOCK_SOURCES_FILE

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 10, 18, 16, 0, tzinfo=UTC)


def _instrument(iid: str = "US_SPY", asset_class: AssetClass = AssetClass.ETF) -> Instrument:
    return Instrument(id=iid, name=iid, asset_class=asset_class, symbols={"yfinance": "X"})


def _bar(day: int, close: float | None, volume: float | None = None) -> PriceBar:
    return PriceBar(
        instrument_id="X",
        observed_at=datetime(2026, 10, day, tzinfo=UTC),
        close=close,
        volume=volume,
    )


# --- cleaning -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (float("nan"), None),
        (float("inf"), None),
        ("", None),
        (0.0, None),
        (-1.0, None),
        ("abc", None),
        (True, None),
        (1.5, 1.5),
        ("2.5", 2.5),
    ],
)
def test_clean_number_never_returns_zero_for_missing(value: object, expected: float | None) -> None:
    assert clean_number(value) == expected


def test_clean_number_allows_non_positive_when_requested() -> None:
    assert clean_number(-0.1, allow_non_positive=True) == -0.1
    assert clean_number(0.0, allow_non_positive=True) == 0.0


def test_clean_bars_dedupes_and_sorts() -> None:
    bars, warnings = clean_bars([_bar(2, 11.0), _bar(1, 10.0), _bar(2, 12.0)], AssetClass.ETF)
    assert [b.close for b in bars] == [10.0, 12.0]
    assert any("duplicate" in w for w in warnings)


def test_clean_bars_invalid_close_becomes_missing() -> None:
    bars, warnings = clean_bars([_bar(1, float("nan")), _bar(2, -3.0)], AssetClass.ETF)
    assert [b.close for b in bars] == [None, None]
    assert len(warnings) == 2


def test_clean_bars_fx_volume_zero_is_missing() -> None:
    bars, _ = clean_bars([_bar(1, 1.1, volume=0.0)], AssetClass.FX)
    assert bars[0].volume is None


def test_clean_bars_yield_may_be_negative() -> None:
    bars, _ = clean_bars([_bar(1, -0.2)], AssetClass.YIELD)
    assert bars[0].close == -0.2


# --- fixture price provider ---------------------------------------------------


def test_fixture_price_provider_reads_mock_data() -> None:
    provider = FixturePriceProvider(MOCK_PRICES_DIR)
    result = provider.get_daily_history(
        _instrument("US_SPY"), date(2026, 7, 1), date(2026, 10, 18), NOW
    )
    assert result.error is None
    assert result.bars
    assert all(b.observed_at.tzinfo is UTC for b in result.bars)
    assert result.bars[-1].observed_at.date() == date(2026, 10, 16)


def test_fixture_price_provider_unknown_symbol_is_error_not_zero() -> None:
    provider = FixturePriceProvider(MOCK_PRICES_DIR)
    result = provider.get_daily_history(
        _instrument("DOES_NOT_EXIST"), date(2026, 7, 1), date(2026, 10, 18), NOW
    )
    assert result.bars == []
    assert result.error is not None


def test_fixture_missing_cell_is_none() -> None:
    provider = FixturePriceProvider(MOCK_PRICES_DIR)
    result = provider.get_daily_history(
        _instrument("DE_EWG"), date(2026, 7, 1), date(2026, 10, 18), NOW
    )
    assert any(b.close is None for b in result.bars)
    assert all(b.close != 0 for b in result.bars)


# --- RSS ------------------------------------------------------------------------

SOURCE = NewsSource(
    id="s1", name="Źródło testowe", url="https://example.com/rss", category="finance"
)


def test_parse_feed_extracts_metadata_and_strips_html() -> None:
    items = parse_feed((FIXTURES / "rss" / "sample.xml").read_bytes(), SOURCE, NOW)
    assert len(items) == 2  # entry without link is skipped
    first = items[0]
    assert first.title == "Zażółć gęślą jaźń: nagłówek"
    assert first.summary == "Krótki lead z HTML & encjami."
    assert first.published_at == datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    assert first.fetched_at == NOW
    assert first.source_name == "Źródło testowe"
    assert items[1].published_at is None
    assert items[1].summary is not None and len(items[1].summary) <= 300


def test_parse_feed_garbage_returns_empty() -> None:
    assert parse_feed(b"not xml at all", SOURCE, NOW) == []


def _rss_provider(handler: httpx.MockTransport) -> RssNewsProvider:
    return RssNewsProvider(client=httpx.Client(transport=handler))


def test_rss_provider_success() -> None:
    body = (FIXTURES / "rss" / "sample.xml").read_bytes()
    provider = _rss_provider(httpx.MockTransport(lambda req: httpx.Response(200, content=body)))
    result = provider.fetch([SOURCE], NOW)
    assert len(result.items) == 2
    assert result.errors == {}


def test_rss_provider_timeout_is_recorded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    result = _rss_provider(httpx.MockTransport(handler)).fetch([SOURCE], NOW)
    assert result.items == []
    assert result.errors == {"s1": "timeout"}


def test_rss_provider_rate_limit_is_recorded() -> None:
    provider = _rss_provider(httpx.MockTransport(lambda req: httpx.Response(429)))
    result = provider.fetch([SOURCE], NOW)
    assert result.errors == {"s1": "HTTP 429"}


def test_fixture_news_provider_reads_mock_feeds() -> None:
    sources = load_sources(MOCK_SOURCES_FILE)
    result = FixtureNewsProvider(MOCK_RSS_DIR).fetch(sources, NOW)
    assert len(result.items) == 5
    assert result.errors == {}
