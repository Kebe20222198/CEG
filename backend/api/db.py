"""SQLite Database setup and SQLAlchemy models for CEG persistence (Section 7.6.1)."""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

# SQLite file next to the ``api`` package unless CEG_DB_PATH says otherwise.
DB_PATH = os.getenv(
    "CEG_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "ceg.db"),
)
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all CEG tables."""


# ── SQLAlchemy Models ─────────────────────────────────────────────────────────


class TaskModel(Base):
    """CognitiveTask persistence table."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    # Registered pipeline template (see api.pipelines); None → graph built
    # from the task's subtasks.
    pipeline: Mapped[str | None] = mapped_column(String, nullable=True)
    task_constraints_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools_allowed_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    subtasks_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evaluation_criteria_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class ExecutionModel(Base):
    """Pipeline execution persistence table.

    status: running, awaiting_approval (HITL pause), completed, failed.
    """

    __tablename__ = "executions"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    scenario_name: Mapped[str] = mapped_column(
        String, nullable=False, default="unnamed"
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="running", index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    total_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    graph_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class TraceModel(Base):
    """Execution trace per node (Composant F, section 7.6.1)."""

    __tablename__ = "traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # completed, skipped, failed
    status: Mapped[str] = mapped_column(String, nullable=False, default="completed")
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_input: Mapped[int] = mapped_column(Integer, default=0)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    decision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallbacks_triggered_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class MetricModel(Base):
    """Evaluation Engine metrics persistence per execution.

    quality_score / robustness_score are NULL when not measured.
    """

    __tablename__ = "metrics"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # execution_id
    execution_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    robustness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    composite_score: Mapped[float] = mapped_column(Float, default=0.0)
    report_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class BenchmarkModel(Base):
    """Benchmark runs persistence (stub S6)."""

    __tablename__ = "benchmarks"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    # ACCEPTED, RUNNING, COMPLETED, FAILED
    status: Mapped[str] = mapped_column(String, nullable=False, default="ACCEPTED")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow)
    scenarios_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    results_json: Mapped[str | None] = mapped_column(Text, nullable=True)


def init_db() -> None:
    """Create missing tables, then add columns introduced since the DB was made.

    ``create_all`` never alters an existing table. Until migrations are
    handled by Alembic, new nullable columns are added here so that an older
    ``ceg.db`` keeps working.
    """
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.exec_driver_sql(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'
                )


def get_db() -> Generator[Session, None, None]:
    """Dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
