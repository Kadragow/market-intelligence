"""SQLAlchemy models.

Raw data (prices, macro observations) is upserted: providers revise history.
Assessments (snapshots), reports, deliveries and errors are append-only; the
repository exposes no way to change or delete them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Dialect,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    TypeDecorator,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Alembic's recommended convention, so constraint names are stable across databases.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UtcDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every backend.

    DECISION: SQLite returns naive datetimes even for DateTime(timezone=True) (verified
    with SQLAlchemy 2.1.6), so values are stored as naive UTC and read back as aware UTC.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not allowed; use timezone-aware UTC")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class InstrumentRow(Base):
    __tablename__ = "instruments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    asset_class: Mapped[str] = mapped_column(String(32))
    exchange: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[str | None] = mapped_column(String(8))
    symbols: Mapped[dict[str, str]] = mapped_column(JSON)
    data_notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class PriceBarRow(Base):
    __tablename__ = "price_bars"

    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    adj_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime())


class MacroObservationRow(Base):
    __tablename__ = "macro_observations"

    series_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), primary_key=True)
    value: Mapped[float | None] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime())


class NewsItemRow(Base):
    __tablename__ = "news_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    link: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(String(64))
    source_name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32))
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), index=True)
    # First time this headline was seen; never overwritten.
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime())
    summary: Mapped[str | None] = mapped_column(Text)


class RunRow(Base):
    """One `mi weekly` execution.

    DECISION: the run row is lifecycle metadata (status and finish time are set when the
    run ends); the assessments it produced live in append-only tables.
    """

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True)
    report_week: Mapped[str] = mapped_column(String(10), index=True)
    mode: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    rules_version: Mapped[str] = mapped_column(String(32))
    config_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(200))
    llm_input_tokens: Mapped[int | None] = mapped_column(Integer)
    llm_output_tokens: Mapped[int | None] = mapped_column(Integer)
    llm_cost: Mapped[float | None] = mapped_column(Float)


class InstrumentSnapshotRow(Base):
    __tablename__ = "instrument_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    instrument_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    last_close: Mapped[float | None] = mapped_column(Float)
    last_observed_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    change_1w_pct: Mapped[float | None] = mapped_column(Float)
    change_1m_pct: Mapped[float | None] = mapped_column(Float)
    return_basis: Mapped[str | None] = mapped_column(String(16))
    bar_count: Mapped[int] = mapped_column(Integer)
    issues: Mapped[list[str]] = mapped_column(JSON)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON)
    rules_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class MacroSnapshotRow(Base):
    __tablename__ = "macro_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    series_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(Text)
    units: Mapped[str | None] = mapped_column(String(200))
    last_value: Mapped[float | None] = mapped_column(Float)
    last_period: Mapped[datetime | None] = mapped_column(UtcDateTime())
    previous_value: Mapped[float | None] = mapped_column(Float)
    change: Mapped[float | None] = mapped_column(Float)
    yoy_change_pct: Mapped[float | None] = mapped_column(Float)
    issues: Mapped[list[str]] = mapped_column(JSON)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON)
    rules_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class ReportRow(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    report_week: Mapped[str] = mapped_column(String(10), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    complete: Mapped[bool] = mapped_column(Boolean)
    subject: Mapped[str] = mapped_column(Text)
    markdown: Mapped[str] = mapped_column(Text)
    html: Mapped[str] = mapped_column(Text)
    # The validated model draft (None when the model output was rejected).
    draft: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    errors: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class DeliveryRow(Base):
    __tablename__ = "deliveries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reports.id"), index=True)
    attempted_at: Mapped[datetime] = mapped_column(UtcDateTime())
    status: Mapped[str] = mapped_column(String(16))
    recipients_count: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class RunErrorRow(Base):
    __tablename__ = "run_errors"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
