from datetime import UTC, date, datetime, timedelta

from market_intelligence.analysis.data_quality import (
    dedupe_news,
    find_price_gaps,
    normalize_title,
    select_news,
)
from market_intelligence.domain.models import NewsItem, PriceBar

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _bar(day: date, close: float | None = 1.0) -> PriceBar:
    return PriceBar(
        instrument_id="X",
        observed_at=datetime(day.year, day.month, day.day, tzinfo=UTC),
        close=close,
    )


def _news(
    link: str,
    title: str,
    *,
    hours_ago: float | None = 1,
    category: str = "finance",
    source: str = "s1",
) -> NewsItem:
    return NewsItem(
        id=NewsItem.make_id(link),
        title=title,
        link=link,
        source_id=source,
        source_name=source,
        category=category,
        published_at=None if hours_ago is None else NOW - timedelta(hours=hours_ago),
        fetched_at=NOW,
    )


def test_single_holiday_is_not_a_gap() -> None:
    # Thu -> Mon skips Friday only (one business day).
    bars = [_bar(date(2026, 10, 22)), _bar(date(2026, 10, 26))]
    assert find_price_gaps(bars, max_missing_business_days=3) == []


def test_long_gap_is_reported() -> None:
    bars = [_bar(date(2026, 10, 1)), _bar(date(2026, 10, 9))]
    issues = find_price_gaps(bars, max_missing_business_days=3)
    assert issues == ["luka w danych: brak 5 dni roboczych między 2026-10-01 a 2026-10-09"]


def test_bars_without_close_do_not_bridge_gaps() -> None:
    bars = [_bar(date(2026, 10, 1)), _bar(date(2026, 10, 5), close=None), _bar(date(2026, 10, 9))]
    assert len(find_price_gaps(bars, max_missing_business_days=3)) == 1


def test_normalize_title() -> None:
    assert normalize_title("  Fed: Stopy BEZ zmian!  ") == normalize_title("fed stopy bez zmian")
    assert normalize_title("Zażółć gęślą jaźń") == "zazołc gesla jazn"


def test_dedupe_by_link_and_title_across_sources() -> None:
    items = [
        _news("https://a.com/1", "Fed holds rates", source="s1"),
        _news("https://a.com/1", "Fed holds rates (updated)", source="s1"),
        _news("https://b.com/x", "FED HOLDS RATES.", source="s2"),
        _news("https://c.com/y", "Something else", source="s2"),
    ]
    unique, dropped = dedupe_news(items)
    assert [i.link for i in unique] == ["https://a.com/1", "https://c.com/y"]
    assert dropped == 2


def test_select_news_window_and_order() -> None:
    items = [
        _news("https://a.com/old", "Old", hours_ago=24 * 10),
        _news("https://a.com/future", "Future", hours_ago=-5),
        _news("https://a.com/undated", "Undated", hours_ago=None),
        _news("https://a.com/new", "New", hours_ago=1),
        _news("https://a.com/mid", "Mid", hours_ago=30),
    ]
    selected, _ = select_news(items, now=NOW, lookback_days=7, max_items=10)
    assert [i.title for i in selected] == ["New", "Mid", "Undated"]


def test_select_news_balances_categories() -> None:
    items = [_news(f"https://ai.com/{n}", f"AI {n}", hours_ago=n, category="ai") for n in range(10)]
    items.append(_news("https://fed.com/1", "Fed", hours_ago=100, category="macro"))
    selected, _ = select_news(items, now=NOW, lookback_days=7, max_items=3)
    assert "Fed" in [i.title for i in selected]
    assert len(selected) == 3
