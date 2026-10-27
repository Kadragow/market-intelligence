"""Data quality checks shared across data types: gaps, duplicates, news selection."""

from __future__ import annotations

import itertools
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import numpy as np

from market_intelligence.domain.models import NewsItem, PriceBar

_NON_WORD_RE = re.compile(r"[\W_]+")


def find_price_gaps(bars: list[PriceBar], max_missing_business_days: int) -> list[str]:
    """Report runs of missing business days between consecutive bars.

    Single exchange holidays are normal, so only runs longer than the threshold count.
    """
    issues: list[str] = []
    dates = sorted({b.observed_at.date() for b in bars if b.close is not None})
    for prev, cur in itertools.pairwise(dates):
        missing = int(np.busday_count(prev, cur)) - 1
        if missing > max_missing_business_days:
            issues.append(
                f"luka w danych: brak {missing} dni roboczych między "
                f"{prev.isoformat()} a {cur.isoformat()}"
            )
    return issues


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _NON_WORD_RE.sub(" ", text).strip()


def dedupe_news(items: list[NewsItem]) -> tuple[list[NewsItem], int]:
    """Drop repeated links and repeated titles (also across sources).

    Keeps the first occurrence, so pass items sorted by preference (newest first).
    Returns (unique items, number dropped).
    """
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        title_key = normalize_title(item.title)
        if item.id in seen_ids or (title_key and title_key in seen_titles):
            continue
        seen_ids.add(item.id)
        if title_key:
            seen_titles.add(title_key)
        unique.append(item)
    return unique, len(items) - len(unique)


def select_news(
    items: list[NewsItem], *, now: datetime, lookback_days: int, max_items: int
) -> tuple[list[NewsItem], int]:
    """Filter to the lookback window, de-duplicate and pick up to ``max_items``.

    Returns (selected items, number of duplicates dropped).
    """
    cutoff = now - timedelta(days=lookback_days)
    # DECISION: items without a publication date are kept (shown as "brak danych");
    # items published outside the window or after the as-of time are dropped.
    in_window = [i for i in items if i.published_at is None or cutoff <= i.published_at <= now]
    oldest = datetime.min.replace(tzinfo=UTC)
    in_window.sort(key=lambda n: n.published_at or oldest, reverse=True)
    unique, dropped = dedupe_news(in_window)

    # DECISION: round-robin across categories (newest first within each), so a few
    # high-volume feeds cannot crowd out macro or semiconductor headlines.
    by_category: dict[str, list[NewsItem]] = defaultdict(list)
    for item in unique:
        by_category[item.category].append(item)
    selected: list[NewsItem] = []
    queues = [by_category[c] for c in sorted(by_category)]
    while len(selected) < max_items and any(queues):
        for queue in queues:
            if queue and len(selected) < max_items:
                selected.append(queue.pop(0))
    selected.sort(key=lambda n: n.published_at or oldest, reverse=True)
    return selected, dropped
