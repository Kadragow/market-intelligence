from datetime import UTC, datetime

import httpx
import pytest

from market_intelligence.data.http import (
    RetryPolicy,
    call_with_retry,
    parse_retry_after,
    request_with_retry,
    safe_url,
)

POLICY = RetryPolicy(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=30.0)
URL = "https://api.example.com/data?api_key=secret123&x=1"


class Script:
    """Mock transport that plays back a list of responses or exceptions."""

    def __init__(self, *steps: int | Exception, headers: dict[str, str] | None = None) -> None:
        self.steps = list(steps)
        self.calls = 0
        self.headers = headers or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        step = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        if isinstance(step, Exception):
            raise step
        return httpx.Response(step, headers=self.headers)


def _run(script: Script, policy: RetryPolicy = POLICY) -> tuple[object, list[float]]:
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(script))
    result = request_with_retry(
        client, "GET", URL, policy=policy, sleep=sleeps.append, jitter=lambda: 1.0
    )
    return result, sleeps


def test_success_first_try() -> None:
    result, sleeps = _run(Script(200))
    assert result.response.status_code == 200  # type: ignore[attr-defined]
    assert result.attempts == 1  # type: ignore[attr-defined]
    assert sleeps == []


def test_retries_5xx_then_succeeds_with_exponential_backoff() -> None:
    result, sleeps = _run(Script(503, 502, 200))
    assert result.response.status_code == 200  # type: ignore[attr-defined]
    assert result.attempts == 3  # type: ignore[attr-defined]
    assert sleeps == [1.0, 2.0]


def test_jitter_scales_delay_between_half_and_full() -> None:
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(Script(500, 200)))
    request_with_retry(client, "GET", URL, policy=POLICY, sleep=sleeps.append, jitter=lambda: 0.0)
    assert sleeps == [0.5]


def test_retry_after_header_is_honoured_and_capped() -> None:
    _, sleeps = _run(Script(429, 200, headers={"Retry-After": "7"}))
    assert sleeps == [7.0]
    _, sleeps = _run(Script(429, 200, headers={"Retry-After": "600"}))
    assert sleeps == [30.0]


def test_gives_up_and_returns_last_response() -> None:
    result, sleeps = _run(Script(503))
    assert result.response.status_code == 503  # type: ignore[attr-defined]
    assert result.attempts == 3  # type: ignore[attr-defined]
    assert len(sleeps) == 2


def test_non_retryable_status_returns_immediately() -> None:
    result, sleeps = _run(Script(404))
    assert result.response.status_code == 404  # type: ignore[attr-defined]
    assert sleeps == []


def test_timeout_is_retried_then_reraised() -> None:
    script = Script(httpx.ReadTimeout("slow"))
    with pytest.raises(httpx.ReadTimeout):
        _run(script)
    assert script.calls == 3


def test_narrow_retry_exceptions() -> None:
    policy = RetryPolicy(retry_exceptions=(httpx.ConnectError,))
    script = Script(httpx.ReadTimeout("slow"))
    with pytest.raises(httpx.ReadTimeout):
        _run(script, policy)
    assert script.calls == 1


def test_logs_do_not_contain_query_string(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING")
    _run(Script(503, 200))
    assert "secret123" not in caplog.text
    assert "https://api.example.com/data" in caplog.text


def test_safe_url_strips_query() -> None:
    assert safe_url(URL) == "https://api.example.com/data"


def test_parse_retry_after() -> None:
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after("Tue, 27 Oct 2026 12:00:10 GMT", now) == 10.0
    assert parse_retry_after("garbage") is None
    assert parse_retry_after(None) is None


def test_call_with_retry() -> None:
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("temporary")
        return "ok"

    sleeps: list[float] = []
    result, attempts = call_with_retry(
        flaky, policy=POLICY, description="test", sleep=sleeps.append, jitter=lambda: 1.0
    )
    assert (result, attempts) == ("ok", 3)
    assert sleeps == [1.0, 2.0]


def test_call_with_retry_reraises_after_last_attempt() -> None:
    def broken() -> str:
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        call_with_retry(broken, policy=POLICY, description="test", sleep=lambda s: None)
