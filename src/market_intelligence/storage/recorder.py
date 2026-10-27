"""Records one weekly run in the repository without letting storage failures stop it.

DECISION (operator, 2026-10-27): if the database fails during a run, the report is still
written to files and sent; the failure is logged and reported (CLI exit code 4).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from market_intelligence.analysis.macro_metrics import MacroSnapshot
from market_intelligence.analysis.price_metrics import InstrumentSnapshot
from market_intelligence.domain.models import Instrument, MacroObservation, NewsItem, PriceBar
from market_intelligence.storage.repository import (
    DeliveryRecord,
    ReportRecord,
    Repository,
    RunFinish,
    RunStart,
    StorageError,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


class RunRecorder:
    def __init__(self, repository: Repository | None, clock: Callable[[], datetime]) -> None:
        self._repo = repository
        self._clock = clock
        self.run_id: uuid.UUID | None = None
        self.errors: list[str] = []

    def fail(self, what: str, message: str) -> None:
        """Record a storage problem that happened outside this recorder (e.g. opening)."""
        logger.error("Storage: %s failed: %s", what, message)
        self.errors.append(f"{what}: {message}")

    def _call(self, what: str, fn: Callable[[Repository], T]) -> T | None:
        if self._repo is None:
            return None
        try:
            return fn(self._repo)
        except StorageError as exc:
            self.fail(what, str(exc))
            return None

    def _with_run(self, what: str, fn: Callable[[Repository, uuid.UUID], T]) -> T | None:
        run_id = self.run_id
        if run_id is None:
            return None
        return self._call(what, lambda repo: fn(repo, run_id))

    # --- run ---------------------------------------------------------------------

    def start(self, run: RunStart) -> None:
        self.run_id = self._call("start run", lambda repo: repo.start_run(run))

    def finish(self, finish: RunFinish) -> None:
        self._with_run("finish run", lambda repo, run_id: repo.finish_run(run_id, finish))

    # --- raw data --------------------------------------------------------------------

    def instruments(self, instruments: list[Instrument]) -> None:
        self._call("save instruments", lambda r: r.upsert_instruments(instruments, self._clock()))

    def price_bars(self, bars: list[PriceBar], provider: str, fetched_at: datetime) -> None:
        self._call(
            "save prices",
            lambda r: r.upsert_price_bars(bars, provider=provider, fetched_at=fetched_at),
        )

    def macro_observations(
        self, observations: list[MacroObservation], provider: str, fetched_at: datetime
    ) -> None:
        self._call(
            "save macro observations",
            lambda r: r.upsert_macro_observations(
                observations, provider=provider, fetched_at=fetched_at
            ),
        )

    def news(self, items: list[NewsItem]) -> None:
        added = self._call("save news", lambda r: r.add_news(items))
        if added is not None:
            logger.info("Storage: %d new headlines saved", added)

    # --- append-only -----------------------------------------------------------------

    def instrument_snapshots(self, snapshots: list[InstrumentSnapshot], rules_version: str) -> None:
        self._with_run(
            "save instrument snapshots",
            lambda r, run_id: r.add_instrument_snapshots(
                run_id, snapshots, rules_version=rules_version, created_at=self._clock()
            ),
        )

    def macro_snapshots(self, snapshots: list[MacroSnapshot], rules_version: str) -> None:
        self._with_run(
            "save macro snapshots",
            lambda r, run_id: r.add_macro_snapshots(
                run_id, snapshots, rules_version=rules_version, created_at=self._clock()
            ),
        )

    def report(self, report: ReportRecord) -> uuid.UUID | None:
        return self._with_run("save report", lambda r, run_id: r.add_report(run_id, report))

    def delivery(self, report_id: uuid.UUID | None, delivery: DeliveryRecord) -> None:
        if report_id is None:
            return
        self._call("save delivery", lambda r: r.add_delivery(report_id, delivery))

    def error(self, stage: str, message: str) -> None:
        self._with_run(
            "save run error",
            lambda r, run_id: r.add_error(
                run_id, stage=stage, message=message, created_at=self._clock()
            ),
        )
