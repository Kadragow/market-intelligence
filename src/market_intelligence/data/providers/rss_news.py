"""RSS/Atom headlines. Stores metadata and a short lead only, never full articles."""

from __future__ import annotations

import calendar
import html
import logging
import re
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from market_intelligence.data.providers.base import NewsFetchResult
from market_intelligence.domain.models import NewsItem, NewsSource

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 300
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_USER_AGENT = "market-intelligence/0.1 (research prototype)"


class RssNewsProvider:
    name = "rss"

    def __init__(
        self,
        timeout_seconds: float = 15.0,
        max_items_per_source: int = 20,
        client: httpx.Client | None = None,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        self._max_items = max_items_per_source

    def fetch(self, sources: list[NewsSource], fetched_at: datetime) -> NewsFetchResult:
        result = NewsFetchResult(items=[])
        for source in sources:
            try:
                response = self._client.get(source.url)
                response.raise_for_status()
            except httpx.TimeoutException:
                result.errors[source.id] = "przekroczony czas oczekiwania"
                logger.warning("RSS timeout for source %s", source.id)
                continue
            except httpx.HTTPStatusError as exc:
                result.errors[source.id] = f"HTTP {exc.response.status_code}"
                logger.warning("RSS HTTP %s for source %s", exc.response.status_code, source.id)
                continue
            except httpx.HTTPError as exc:
                result.errors[source.id] = f"błąd połączenia: {type(exc).__name__}"
                logger.warning("RSS error for source %s: %s", source.id, type(exc).__name__)
                continue

            items = parse_feed(response.content, source, fetched_at)
            if not items:
                result.errors[source.id] = "kanał nie zawierał poprawnych wpisów"
            result.items.extend(items[: self._max_items])
        return result


def parse_feed(content: bytes, source: NewsSource, fetched_at: datetime) -> list[NewsItem]:
    parsed: Any = feedparser.parse(content)
    items: list[NewsItem] = []
    for entry in parsed.entries:
        title = _clean_text(entry.get("title"))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        summary = _clean_text(entry.get("summary"))
        if summary and len(summary) > MAX_SUMMARY_CHARS:
            summary = summary[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"
        items.append(
            NewsItem(
                id=NewsItem.make_id(link),
                title=title,
                link=link,
                source_id=source.id,
                source_name=source.name,
                category=source.category,
                published_at=_entry_time(entry),
                fetched_at=fetched_at,
                summary=summary or None,
            )
        )
    return items


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = html.unescape(_TAG_RE.sub(" ", value))
    return _WS_RE.sub(" ", text).strip()


def _entry_time(entry: Any) -> datetime | None:
    # feedparser normalises *_parsed fields to UTC struct_time.
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)
    return None
