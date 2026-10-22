"""LLM client for the OpenRouter chat completions API (plain HTTP via httpx).

Based on the OpenRouter docs read 2026-10-22:
- POST https://openrouter.ai/api/v1/chat/completions, ``Authorization: Bearer <key>``;
- structured output via ``response_format: {type: json_schema, json_schema: {...}}``;
  ``provider.require_parameters: true`` routes only to endpoints that support it;
- ``provider.max_price`` (USD per million tokens) caps the provider price;
- errors may arrive with HTTP 200 inside the body, so the body is always checked;
- there is no pre-call token counting endpoint.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, SecretStr

from market_intelligence.ai.client import CostLimitExceededError, LLMError, LLMResponse

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
APP_TITLE = "market-intelligence"

# DECISION: OpenRouter has no token counting endpoint. Input tokens are estimated
# conservatively as one token per 3 characters (real tokenizers average more
# characters per token, so this over-estimates the cost).
CHARS_PER_TOKEN_ESTIMATE = 3

_STATUS_MESSAGES = {
    400: "bad request (check model name and parameters)",
    401: "invalid or missing OPENROUTER_API_KEY",
    402: "insufficient credits",
    403: "request blocked (moderation, guardrail or budget limit)",
    404: "model not found or no provider can serve the request",
    408: "request timed out",
    429: "rate limited",
    502: "model provider is down or returned an invalid response",
    503: "no provider meets the routing requirements "
    "(structured outputs, max price or data policy)",
}
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)

DataCollection = Literal["allow", "deny"]


class OpenRouterLLMClient:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        max_tokens: int,
        max_cost_usd: float,
        input_usd_per_mtok: float,
        output_usd_per_mtok: float,
        timeout_seconds: float,
        data_collection: DataCollection,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._model = model
        self._max_tokens = max_tokens
        self._max_cost = max_cost_usd
        self._in_per_mtok = input_usd_per_mtok
        self._out_per_mtok = output_usd_per_mtok
        self._data_collection = data_collection

    def estimate_max_cost(self, system: str, user: str) -> float:
        """Upper bound: estimated input tokens plus the full ``max_tokens`` budget."""
        input_tokens = math.ceil((len(system) + len(user)) / CHARS_PER_TOKEN_ESTIMATE)
        return (
            input_tokens * self._in_per_mtok + self._max_tokens * self._out_per_mtok
        ) / 1_000_000

    def build_request(
        self, *, system: str, user: str, output_model: type[BaseModel]
    ) -> dict[str, Any]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self._max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": output_model.__name__,
                    "strict": True,
                    "schema": output_model.model_json_schema(),
                },
            },
            "provider": {
                # Never route to an endpoint that would ignore the JSON schema.
                "require_parameters": True,
                # DECISION: the configured prices are enforced by OpenRouter, so the
                # pre-call cost estimate holds whichever provider serves the request.
                "max_price": {"prompt": self._in_per_mtok, "completion": self._out_per_mtok},
                "data_collection": self._data_collection,
            },
        }

    def generate(self, *, system: str, user: str, output_model: type[BaseModel]) -> LLMResponse:
        estimate = self.estimate_max_cost(system, user)
        logger.info(
            "LLM pre-check: model=%s max cost estimate %.4f USD (limit %.2f)",
            self._model,
            estimate,
            self._max_cost,
        )
        if estimate > self._max_cost:
            raise CostLimitExceededError(
                f"estimated max cost {estimate:.4f} USD exceeds limit {self._max_cost:.2f} USD"
            )

        try:
            response = self._client.post(
                OPENROUTER_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                    "X-OpenRouter-Title": APP_TITLE,
                },
                json=self.build_request(system=system, user=user, output_model=output_model),
            )
        except httpx.TimeoutException as exc:
            raise LLMError("OpenRouter timeout") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenRouter connection error: {type(exc).__name__}") from exc

        body = _json_body(response)
        if response.status_code != 200 or "error" in body:
            raise LLMError(
                _error_message(response.status_code, body, self._api_key.get_secret_value())
            )
        return self._parse_success(body)

    def _parse_success(self, body: dict[str, Any]) -> LLMResponse:
        choices = body.get("choices") or []
        if not choices:
            raise LLMError("OpenRouter returned no choices")
        choice = choices[0]
        if choice.get("error"):
            raise LLMError(
                _error_message(200, {"error": choice["error"]}, self._api_key.get_secret_value())
            )
        message = choice.get("message") or {}
        finish = choice.get("finish_reason")
        if message.get("refusal") or finish == "content_filter":
            raise LLMError("model refused the request")
        if finish == "length":
            raise LLMError("model output truncated (max_tokens reached)")
        if finish == "error":
            raise LLMError("model provider reported an error")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMError("model returned no content")

        usage = body.get("usage") or {}
        cost = usage.get("cost")
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        logger.info(
            "LLM call done: model=%s in=%s out=%s cost=%s credits",
            body.get("model"),
            input_tokens,
            output_tokens,
            cost,
        )
        return LLMResponse(
            text=_strip_code_fence(content),
            model=str(body.get("model") or self._model),
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            # OpenRouter reports cost in credits; credits are bought in USD.
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
        )


def _json_body(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


MAX_ERROR_DETAIL_CHARS = 200
# Structured, short metadata fields documented for 403/402 errors.
_METADATA_FIELDS = ("error_type", "limit_source", "reasons", "provider_name", "model_slug")


def _error_message(status: int, body: dict[str, Any], secret: str = "") -> str:
    raw_error = body.get("error")
    error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
    raw_code = error.get("code")
    code = raw_code if isinstance(raw_code, int) else status
    hint = _STATUS_MESSAGES.get(code, "unexpected error")
    result = f"OpenRouter error: HTTP {code} ({hint})"

    # DECISION: include a short, single-line diagnostic (metadata + truncated message)
    # so 403/400 causes are visible. It is only logged and shown in the failure report,
    # never sent to the model; the API key is redacted defensively.
    details: list[str] = []
    metadata = error.get("metadata")
    if isinstance(metadata, dict):
        for name in _METADATA_FIELDS:
            value = metadata.get(name)
            if isinstance(value, str | int | float | list) and value not in ("", []):
                details.append(f"{name}={value}")
    message = error.get("message")
    if isinstance(message, str) and message.strip():
        details.append(f"message={message.strip()}")
    if not details:
        return result
    detail = " ".join("; ".join(details).split())
    if secret:
        detail = detail.replace(secret, "***")
    if len(detail) > MAX_ERROR_DETAIL_CHARS:
        detail = detail[: MAX_ERROR_DETAIL_CHARS - 1] + "…"
    return f"{result}: {detail}"


def _strip_code_fence(content: str) -> str:
    # DECISION: some models wrap JSON in a Markdown code fence even with a schema.
    match = _CODE_FENCE_RE.match(content.strip())
    return match.group(1) if match else content
