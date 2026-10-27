"""HTTP and call retries with exponential backoff, jitter and Retry-After support.

URLs are logged without their query string: some APIs (FRED) take the key there.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

Sleep = Callable[[float], None]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    # DECISION: by default every transport error (timeouts included) is retried. Clients
    # where a retry could be billed twice (LLM) pass a narrower tuple.
    retry_exceptions: tuple[type[Exception], ...] = (httpx.TransportError,)


@dataclass
class HttpResult:
    response: httpx.Response
    attempts: int


@dataclass
class _Backoff:
    policy: RetryPolicy
    sleep: Sleep
    jitter: Callable[[], float] = field(default=random.random)

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.policy.max_delay_seconds)
        raw = self.policy.base_delay_seconds * float(2 ** (attempt - 1))
        # Full delay scaled into [50%, 100%] to spread concurrent retries.
        return min(raw, self.policy.max_delay_seconds) * (0.5 + self.jitter() / 2)


def safe_url(url: str | httpx.URL) -> str:
    parts = urlsplit(str(url))
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - (now or datetime.now(UTC))).total_seconds(), 0.0)


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    policy: RetryPolicy,
    sleep: Sleep = time.sleep,
    jitter: Callable[[], float] = random.random,
    **kwargs: Any,
) -> HttpResult:
    """Send a request, retrying transient failures.

    Returns the last response (which may still carry a retryable status once attempts
    are exhausted). Re-raises the last exception if every attempt raised.
    """
    backoff = _Backoff(policy, sleep, jitter)
    for attempt in range(1, policy.max_attempts + 1):
        last = attempt == policy.max_attempts
        try:
            response = client.request(method, url, **kwargs)
        except policy.retry_exceptions as exc:
            if last:
                raise
            wait = backoff.delay(attempt)
            logger.warning(
                "%s %s attempt %d/%d failed (%s); retrying in %.1fs",
                method,
                safe_url(url),
                attempt,
                policy.max_attempts,
                type(exc).__name__,
                wait,
            )
            sleep(wait)
            continue
        if response.status_code not in policy.retry_statuses or last:
            return HttpResult(response, attempt)
        wait = backoff.delay(attempt, parse_retry_after(response.headers.get("Retry-After")))
        logger.warning(
            "%s %s attempt %d/%d returned HTTP %d; retrying in %.1fs",
            method,
            safe_url(url),
            attempt,
            policy.max_attempts,
            response.status_code,
            wait,
        )
        sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


def call_with_retry[T](
    fn: Callable[[], T],
    *,
    policy: RetryPolicy,
    description: str,
    retry_exceptions: tuple[type[Exception], ...] = (Exception,),
    sleep: Sleep = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> tuple[T, int]:
    """Call ``fn`` until it succeeds or attempts run out. Returns (result, attempts)."""
    backoff = _Backoff(policy, sleep, jitter)
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn(), attempt
        except retry_exceptions as exc:
            if attempt == policy.max_attempts:
                raise
            wait = backoff.delay(attempt)
            logger.warning(
                "%s attempt %d/%d failed (%s); retrying in %.1fs",
                description,
                attempt,
                policy.max_attempts,
                type(exc).__name__,
                wait,
            )
            sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover
