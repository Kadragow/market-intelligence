"""SQLAlchemy implementation of the repository (SQLite now, PostgreSQL later)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, Table, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from market_intelligence.analysis.macro_metrics import MacroSnapshot
from market_intelligence.analysis.price_metrics import InstrumentSnapshot
from market_intelligence.domain.models import Instrument, MacroObservation, NewsItem, PriceBar
from market_intelligence.storage.models import (
    Base,
    DeliveryRow,
    InstrumentRow,
    InstrumentSnapshotRow,
    MacroObservationRow,
    MacroSnapshotRow,
    NewsItemRow,
    PriceBarRow,
    ReportRow,
    RunErrorRow,
    RunRow,
)
from market_intelligence.storage.repository import (
    DeliveryRecord,
    ReportRecord,
    RunFinish,
    RunStart,
    RunSummary,
    StorageError,
)

# Keeps single statements well below SQLite's bound-parameter limit.
_CHUNK = 200


class SqlRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        dialect = engine.dialect.name
        if dialect == "sqlite":
            self._insert: Callable[[Table], Any] = sqlite.insert
        elif dialect == "postgresql":
            self._insert = postgresql.insert
        else:
            raise StorageError(f"unsupported database dialect: {dialect}")

    @contextmanager
    def _session(self) -> Iterator[Session]:
        try:
            with Session(self._engine) as session, session.begin():
                yield session
        except SQLAlchemyError as exc:
            # The exception text may contain SQL and parameters; keep only the type.
            raise StorageError(f"database error: {type(exc).__name__}") from exc

    def _upsert(
        self,
        table: Table,
        rows: list[dict[str, Any]],
        keys: Sequence[str],
        update: Sequence[str] | None,
    ) -> int:
        if not rows:
            return 0
        written = 0
        with self._session() as session:
            for start in range(0, len(rows), _CHUNK):
                chunk = rows[start : start + _CHUNK]
                stmt = self._insert(table).values(chunk)
                if update is None:
                    stmt = stmt.on_conflict_do_nothing(index_elements=list(keys))
                else:
                    stmt = stmt.on_conflict_do_update(
                        index_elements=list(keys),
                        set_={name: stmt.excluded[name] for name in update},
                    )
                result = session.execute(stmt)
                written += max(getattr(result, "rowcount", 0) or 0, 0)
        return written

    # --- raw data ------------------------------------------------------------------

    def upsert_instruments(self, instruments: list[Instrument], updated_at: datetime) -> None:
        rows = [
            {
                "id": i.id,
                "name": i.name,
                "asset_class": i.asset_class.value,
                "exchange": i.exchange,
                "currency": i.currency,
                "symbols": dict(i.symbols),
                "data_notes": i.data_notes,
                "updated_at": updated_at,
            }
            for i in instruments
        ]
        columns = ["name", "asset_class", "exchange", "currency", "symbols", "data_notes"]
        self._upsert(_table(InstrumentRow), rows, ["id"], [*columns, "updated_at"])

    def upsert_price_bars(
        self, bars: list[PriceBar], *, provider: str, fetched_at: datetime
    ) -> int:
        values = ["open", "high", "low", "close", "adj_close", "volume"]
        rows = [
            {
                "instrument_id": b.instrument_id,
                "provider": provider,
                "observed_at": b.observed_at,
                **{name: getattr(b, name) for name in values},
                "fetched_at": fetched_at,
            }
            for b in _unique(bars, lambda b: (b.instrument_id, b.observed_at))
        ]
        # DECISION: raw prices are upserted; providers revise history, the latest fetch wins.
        return self._upsert(
            _table(PriceBarRow),
            rows,
            ["instrument_id", "provider", "observed_at"],
            [*values, "fetched_at"],
        )

    def upsert_macro_observations(
        self, observations: list[MacroObservation], *, provider: str, fetched_at: datetime
    ) -> int:
        rows = [
            {
                "series_id": o.series_id,
                "provider": provider,
                "observed_at": o.observed_at,
                "value": o.value,
                "fetched_at": fetched_at,
            }
            for o in _unique(observations, lambda o: (o.series_id, o.observed_at))
        ]
        return self._upsert(
            _table(MacroObservationRow),
            rows,
            ["series_id", "provider", "observed_at"],
            ["value", "fetched_at"],
        )

    def add_news(self, items: list[NewsItem]) -> int:
        rows = [
            {
                "id": n.id,
                "title": n.title,
                "link": n.link,
                "source_id": n.source_id,
                "source_name": n.source_name,
                "category": n.category,
                "published_at": n.published_at,
                "fetched_at": n.fetched_at,
                "summary": n.summary,
            }
            for n in _unique(items, lambda n: n.id)
        ]
        return self._upsert(_table(NewsItemRow), rows, ["id"], None)

    # --- runs ----------------------------------------------------------------------

    def start_run(self, run: RunStart) -> uuid.UUID:
        run_id = uuid.uuid4()
        with self._session() as session:
            session.add(
                RunRow(
                    id=run_id,
                    report_week=run.report_week,
                    mode=run.mode,
                    status="running",
                    started_at=run.started_at,
                    rules_version=run.rules_version,
                    config_hash=run.config_hash,
                )
            )
        return run_id

    def finish_run(self, run_id: uuid.UUID, finish: RunFinish) -> None:
        with self._session() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise StorageError(f"run {run_id} not found")
            row.status = finish.status
            row.finished_at = finish.finished_at
            row.model = finish.model
            row.llm_input_tokens = finish.llm_input_tokens
            row.llm_output_tokens = finish.llm_output_tokens
            row.llm_cost = finish.llm_cost

    # --- append-only ---------------------------------------------------------------

    def add_instrument_snapshots(
        self,
        run_id: uuid.UUID,
        snapshots: list[InstrumentSnapshot],
        *,
        rules_version: str,
        created_at: datetime,
    ) -> list[uuid.UUID]:
        rows = [
            InstrumentSnapshotRow(
                id=uuid.uuid4(),
                run_id=run_id,
                instrument_id=s.instrument.id,
                status=s.status.value,
                last_close=s.last_close,
                last_observed_at=s.last_observed_at,
                change_1w_pct=s.change_1w_pct,
                change_1m_pct=s.change_1m_pct,
                return_basis=s.return_basis,
                bar_count=s.bar_count,
                issues=list(s.issues),
                provenance=s.provenance.model_dump(mode="json"),
                rules_version=rules_version,
                created_at=created_at,
            )
            for s in snapshots
        ]
        return self._add_all(rows)

    def add_macro_snapshots(
        self,
        run_id: uuid.UUID,
        snapshots: list[MacroSnapshot],
        *,
        rules_version: str,
        created_at: datetime,
    ) -> list[uuid.UUID]:
        rows = [
            MacroSnapshotRow(
                id=uuid.uuid4(),
                run_id=run_id,
                series_id=m.series.id,
                status=m.status.value,
                title=m.title,
                units=m.units,
                last_value=m.last_value,
                last_period=m.last_period,
                previous_value=m.previous_value,
                change=m.change,
                yoy_change_pct=m.yoy_change_pct,
                issues=list(m.issues),
                provenance=m.provenance.model_dump(mode="json"),
                rules_version=rules_version,
                created_at=created_at,
            )
            for m in snapshots
        ]
        return self._add_all(rows)

    def add_report(self, run_id: uuid.UUID, report: ReportRecord) -> uuid.UUID:
        row = ReportRow(
            id=uuid.uuid4(),
            run_id=run_id,
            report_week=report.report_week,
            kind=report.kind,
            complete=report.complete,
            subject=report.subject,
            markdown=report.markdown,
            html=report.html,
            draft=report.draft,
            errors=list(report.errors),
            created_at=report.created_at,
        )
        return self._add_all([row])[0]

    def add_delivery(self, report_id: uuid.UUID, delivery: DeliveryRecord) -> uuid.UUID:
        row = DeliveryRow(
            id=uuid.uuid4(),
            report_id=report_id,
            attempted_at=delivery.attempted_at,
            status=delivery.status,
            recipients_count=delivery.recipients_count,
            error=delivery.error,
        )
        return self._add_all([row])[0]

    def add_error(
        self, run_id: uuid.UUID, *, stage: str, message: str, created_at: datetime
    ) -> uuid.UUID:
        row = RunErrorRow(
            id=uuid.uuid4(), run_id=run_id, stage=stage, message=message, created_at=created_at
        )
        return self._add_all([row])[0]

    def _add_all(self, rows: Sequence[Any]) -> list[uuid.UUID]:
        ids: list[uuid.UUID] = [row.id for row in rows]
        if rows:
            with self._session() as session:
                session.add_all(rows)
        return ids

    # --- reads ---------------------------------------------------------------------

    def recent_runs(self, limit: int = 10) -> list[RunSummary]:
        with self._session() as session:
            rows = session.scalars(
                select(RunRow).order_by(RunRow.started_at.desc()).limit(limit)
            ).all()
            return [
                RunSummary(
                    id=r.id,
                    report_week=r.report_week,
                    mode=r.mode,
                    status=r.status,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                    rules_version=r.rules_version,
                    model=r.model,
                )
                for r in rows
            ]


def _unique[T](items: Sequence[T], key: Callable[[T], object]) -> list[T]:
    """Keep the last item per key (a single INSERT cannot contain the same key twice)."""
    by_key: dict[object, T] = {}
    for item in items:
        by_key[key(item)] = item
    return list(by_key.values())


def _table(row_class: type[Base]) -> Table:
    return Base.metadata.tables[row_class.__tablename__]
