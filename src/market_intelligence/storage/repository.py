"""Repository interface. Business logic depends on this, not on SQLAlchemy.

Append-only records (snapshots, reports, deliveries, errors) have ``add_*`` methods
only: there is deliberately no way to update or delete them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from market_intelligence.analysis.macro_metrics import MacroSnapshot
from market_intelligence.analysis.price_metrics import InstrumentSnapshot
from market_intelligence.domain.models import Instrument, MacroObservation, NewsItem, PriceBar


class StorageError(Exception):
    """A storage operation failed. The message is safe to log."""


@dataclass(frozen=True)
class RunStart:
    report_week: str
    mode: str  # "mock" | "live"
    started_at: datetime
    rules_version: str
    config_hash: str


@dataclass(frozen=True)
class RunFinish:
    status: str  # "ok" | "failed"
    finished_at: datetime
    model: str | None = None
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None
    llm_cost: float | None = None


@dataclass(frozen=True)
class ReportRecord:
    report_week: str
    kind: str
    complete: bool
    subject: str
    markdown: str
    html: str
    draft: dict[str, Any] | None
    errors: list[str]
    created_at: datetime


@dataclass(frozen=True)
class DeliveryRecord:
    attempted_at: datetime
    status: str  # "sent" | "failed"
    recipients_count: int
    error: str | None = None


@dataclass(frozen=True)
class RunSummary:
    id: uuid.UUID
    report_week: str
    mode: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    rules_version: str
    model: str | None


class Repository(Protocol):
    # Raw data (upserted).
    def upsert_instruments(self, instruments: list[Instrument], updated_at: datetime) -> None: ...

    def upsert_price_bars(
        self, bars: list[PriceBar], *, provider: str, fetched_at: datetime
    ) -> int: ...

    def upsert_macro_observations(
        self, observations: list[MacroObservation], *, provider: str, fetched_at: datetime
    ) -> int: ...

    def add_news(self, items: list[NewsItem]) -> int:
        """Insert unseen headlines; returns how many were new."""
        ...

    # Run lifecycle.
    def start_run(self, run: RunStart) -> uuid.UUID: ...

    def finish_run(self, run_id: uuid.UUID, finish: RunFinish) -> None: ...

    # Append-only records.
    def add_instrument_snapshots(
        self,
        run_id: uuid.UUID,
        snapshots: list[InstrumentSnapshot],
        *,
        rules_version: str,
        created_at: datetime,
    ) -> list[uuid.UUID]: ...

    def add_macro_snapshots(
        self,
        run_id: uuid.UUID,
        snapshots: list[MacroSnapshot],
        *,
        rules_version: str,
        created_at: datetime,
    ) -> list[uuid.UUID]: ...

    def add_report(self, run_id: uuid.UUID, report: ReportRecord) -> uuid.UUID: ...

    def add_delivery(self, report_id: uuid.UUID, delivery: DeliveryRecord) -> uuid.UUID: ...

    def add_error(
        self, run_id: uuid.UUID, *, stage: str, message: str, created_at: datetime
    ) -> uuid.UUID: ...

    # Reads.
    def recent_runs(self, limit: int = 10) -> list[RunSummary]: ...
