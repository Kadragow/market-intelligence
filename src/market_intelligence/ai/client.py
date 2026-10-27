"""LLM client interface and an offline fake."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


class LLMError(Exception):
    """The model call failed or returned unusable output. Message is safe to log."""


class CostLimitExceededError(LLMError):
    """The estimated cost of the call exceeds the configured per-run limit."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class LLMClient(Protocol):
    def generate(self, *, system: str, user: str, output_model: type[BaseModel]) -> LLMResponse:
        """Return JSON text that is expected to match ``output_model``."""
        ...


class FakeLLMClient:
    """Returns whatever ``responder`` produces. No network, no API key."""

    def __init__(self, responder: Callable[[str, str], str], model: str = "fake") -> None:
        self._responder = responder
        self._model = model
        self.calls: list[tuple[str, str]] = []

    def generate(self, *, system: str, user: str, output_model: type[BaseModel]) -> LLMResponse:
        self.calls.append((system, user))
        return LLMResponse(text=self._responder(system, user), model=self._model)
