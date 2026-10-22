import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from market_intelligence.ai.client import CostLimitExceededError, LLMError
from market_intelligence.ai.openrouter_client import OPENROUTER_CHAT_URL, OpenRouterLLMClient
from market_intelligence.ai.schema import ReportDraft

API_KEY = "sk-or-test-secret"
DRAFT_JSON = json.dumps(
    {
        "summary": [{"text": "Tekst.", "source_ids": ["price-US_SPY"]}],
        "global_markets": [],
        "macro": [],
        "sectors_and_companies": [],
        "research_candidates": [],
        "warning_signs": [],
        "technology": [],
        "scenarios": {
            name: {"text": "Tekst.", "source_ids": ["price-US_SPY"]}
            for name in ("positive", "base", "negative")
        },
        "limitations": ["Ograniczenie."],
    }
)


def _ok_body(content: str = DRAFT_JSON, finish: str = "stop") -> dict[str, Any]:
    return {
        "id": "gen-1",
        "model": "anthropic/claude-sonnet-5.5",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 2000, "cost": 0.022},
    }


class Recorder:
    def __init__(self, status: int = 200, body: Any = None, error: Exception | None = None):
        self.requests: list[httpx.Request] = []
        self._status = status
        self._body = _ok_body() if body is None else body
        self._error = error

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._error:
            raise self._error
        return httpx.Response(self._status, json=self._body)

    @property
    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.requests[0].content)
        return result


def _client(
    recorder: Recorder,
    *,
    max_cost: float = 1.0,
    prices: tuple[float, float] = (2.0, 10.0),
    model: str = "anthropic/claude-sonnet-5.5",
) -> OpenRouterLLMClient:
    return OpenRouterLLMClient(
        api_key=SecretStr(API_KEY),
        model=model,
        max_tokens=16000,
        max_cost_usd=max_cost,
        input_usd_per_mtok=prices[0],
        output_usd_per_mtok=prices[1],
        timeout_seconds=10,
        data_collection="allow",
        client=httpx.Client(transport=httpx.MockTransport(recorder)),
    )


def _generate(client: OpenRouterLLMClient) -> Any:
    return client.generate(system="system", user="user", output_model=ReportDraft)


def test_success_request_shape_and_response() -> None:
    rec = Recorder()
    resp = _generate(_client(rec))
    assert ReportDraft.model_validate_json(resp.text)
    assert resp.model == "anthropic/claude-sonnet-5.5"
    assert (resp.input_tokens, resp.output_tokens, resp.cost_usd) == (1000, 2000, 0.022)

    request = rec.requests[0]
    assert str(request.url) == OPENROUTER_CHAT_URL
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    payload = rec.payload
    assert payload["model"] == "anthropic/claude-sonnet-5.5"
    assert payload["max_tokens"] == 16000
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    fmt = payload["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == ReportDraft.model_json_schema()
    assert payload["provider"] == {
        "require_parameters": True,
        "max_price": {"prompt": 2.0, "completion": 10.0},
        "data_collection": "allow",
    }
    assert "temperature" not in payload


def test_free_model_with_zero_prices_passes_cost_check() -> None:
    rec = Recorder()
    _generate(_client(rec, max_cost=0.01, prices=(0.0, 0.0), model="some/model:free"))
    assert rec.payload["provider"]["max_price"] == {"prompt": 0.0, "completion": 0.0}


def test_cost_limit_blocks_call_before_request() -> None:
    rec = Recorder()
    # 16000 output tokens * 10 USD/M = 0.16 USD > 0.10
    with pytest.raises(CostLimitExceededError):
        _generate(_client(rec, max_cost=0.10))
    assert rec.requests == []


def test_code_fence_is_stripped() -> None:
    rec = Recorder(body=_ok_body(content=f"```json\n{DRAFT_JSON}\n```"))
    assert ReportDraft.model_validate_json(_generate(_client(rec)).text)


@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        (401, "HTTP 401 (invalid or missing OPENROUTER_API_KEY)"),
        (402, "HTTP 402 (insufficient credits)"),
        (429, "HTTP 429 (rate limited)"),
        (503, "HTTP 503 (no provider meets the routing requirements"),
    ],
)
def test_http_errors(status: int, fragment: str) -> None:
    body = {"error": {"code": status, "message": f"provider says {API_KEY}"}}
    with pytest.raises(LLMError) as info:
        _generate(_client(Recorder(status=status, body=body)))
    assert fragment in str(info.value)
    assert API_KEY not in str(info.value)


def test_error_inside_http_200_body() -> None:
    body = {"error": {"code": 502, "message": "upstream failed"}}
    with pytest.raises(LLMError, match="HTTP 502"):
        _generate(_client(Recorder(body=body)))


def test_error_inside_choice() -> None:
    body = _ok_body(finish="error")
    body["choices"][0]["error"] = {"code": 502, "message": "boom"}
    with pytest.raises(LLMError, match="HTTP 502"):
        _generate(_client(Recorder(body=body)))


@pytest.mark.parametrize(
    ("finish", "content", "message"),
    [
        ("length", DRAFT_JSON, "truncated"),
        ("content_filter", DRAFT_JSON, "refused"),
        ("error", DRAFT_JSON, "provider reported an error"),
        ("stop", "", "no content"),
    ],
)
def test_bad_finish_reasons(finish: str, content: str, message: str) -> None:
    rec = Recorder(body=_ok_body(content=content, finish=finish))
    with pytest.raises(LLMError, match=message):
        _generate(_client(rec))


def test_refusal_field() -> None:
    body = _ok_body()
    body["choices"][0]["message"]["refusal"] = "I can't help with that."
    with pytest.raises(LLMError, match="refused"):
        _generate(_client(Recorder(body=body)))


def test_no_choices() -> None:
    with pytest.raises(LLMError, match="no choices"):
        _generate(_client(Recorder(body={"choices": []})))


def test_timeout_and_connection_errors() -> None:
    with pytest.raises(LLMError, match="timeout"):
        _generate(_client(Recorder(error=httpx.ReadTimeout("slow"))))
    with pytest.raises(LLMError, match="connection error: ConnectError"):
        _generate(_client(Recorder(error=httpx.ConnectError("refused"))))


def test_non_json_error_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"<html>Bad gateway</html>")

    client = OpenRouterLLMClient(
        api_key=SecretStr(API_KEY),
        model="m",
        max_tokens=1024,
        max_cost_usd=1.0,
        input_usd_per_mtok=1.0,
        output_usd_per_mtok=1.0,
        timeout_seconds=10,
        data_collection="deny",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(LLMError, match="HTTP 502"):
        _generate(client)
