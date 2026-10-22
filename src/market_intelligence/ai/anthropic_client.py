"""Claude API client (official Anthropic Python SDK) with a per-run cost limit.

Structured output uses ``messages.parse(output_format=<Pydantic model>)``, which
sends the JSON schema in ``output_config.format`` and parses the reply.
"""

from __future__ import annotations

import logging

import anthropic
from anthropic.types.beta import BetaMessageParam
from pydantic import BaseModel, SecretStr, ValidationError

from market_intelligence.ai.client import CostLimitExceededError, LLMError, LLMResponse

logger = logging.getLogger(__name__)

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLMClient:
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
        refusal_fallback: bool,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self._client = client or anthropic.Anthropic(
            api_key=api_key.get_secret_value(), timeout=timeout_seconds
        )
        self._model = model
        self._max_tokens = max_tokens
        self._max_cost = max_cost_usd
        self._in_price = input_usd_per_mtok / 1_000_000
        self._out_price = output_usd_per_mtok / 1_000_000
        self._refusal_fallback = refusal_fallback

    def estimate_max_cost(self, input_tokens: int) -> float:
        """Upper bound: all input tokens plus the full ``max_tokens`` budget."""
        return input_tokens * self._in_price + self._max_tokens * self._out_price

    def generate(self, *, system: str, user: str, output_model: type[BaseModel]) -> LLMResponse:
        messages: list[BetaMessageParam] = [{"role": "user", "content": user}]
        try:
            count = self._client.messages.count_tokens(
                model=self._model,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_model,
            )
            estimate = self.estimate_max_cost(count.input_tokens)
            logger.info(
                "LLM pre-check: %d input tokens, max cost estimate %.4f USD (limit %.2f)",
                count.input_tokens,
                estimate,
                self._max_cost,
            )
            if estimate > self._max_cost:
                raise CostLimitExceededError(
                    f"estimated max cost {estimate:.4f} USD exceeds limit {self._max_cost:.2f} USD"
                )

            response = self._client.beta.messages.parse(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=messages,
                output_format=output_model,
                betas=[REFUSAL_FALLBACK_BETA] if self._refusal_fallback else anthropic.omit,
                fallbacks="default" if self._refusal_fallback else anthropic.omit,
            )
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error: HTTP {exc.status_code}") from exc
        except anthropic.APITimeoutError as exc:
            raise LLMError("Claude API timeout") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Claude API connection error") from exc
        except ValidationError as exc:
            raise LLMError(
                f"model output does not match schema: {exc.error_count()} errors"
            ) from exc

        if response.stop_reason == "refusal":
            raise LLMError("model refused the request")
        if response.stop_reason == "max_tokens":
            raise LLMError("model output truncated (max_tokens reached)")
        if response.parsed_output is None:
            raise LLMError("model returned no structured output")

        usage = response.usage
        cost = usage.input_tokens * self._in_price + usage.output_tokens * self._out_price
        if usage.iterations and any(i.type == "fallback_message" for i in usage.iterations):
            logger.warning(
                "Request was served by a fallback model (%s); cost uses %s prices",
                response.model,
                self._model,
            )
        logger.info(
            "LLM call done: model=%s in=%d out=%d cost=%.4f USD",
            response.model,
            usage.input_tokens,
            usage.output_tokens,
            cost,
        )
        return LLMResponse(
            text=response.parsed_output.model_dump_json(),
            model=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
        )
