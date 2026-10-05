"""SQLite Database setup and SQLAlchemy models for CEG persistence (Section 7.6.1)."""

from datetime import datetime, timezone
import json
import os
from typing import Any, Generator

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# SQLite file path in root workspace
DB_PATH = os.getenv("CEG_DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "ceg.db"))
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# ── SQLAlchemy Models ─────────────────────────────────────────────────────────

class TaskModel(Base):
    """CognitiveTask persistence table."""

    __tablename__ = "tasks"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=True)
    objective = Column(Text, nullable=False)
    task_constraints_json = Column(Text, nullable=True)
    tools_allowed_json = Column(Text, nullable=False, default="[]")
    subtasks_json = Column(Text, nullable=False, default="[]")
    evaluation_criteria_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ExecutionModel(Base):
    """Pipeline execution persistence table."""

    __tablename__ = "executions"

    id = Column(String, primary_key=True, index=True)
    task_id = Column(String, nullable=True, index=True)
    scenario_name = Column(String, nullable=False, default="unnamed")
    status = Column(String, nullable=False, default="pending", index=True)  # pending, running, completed, failed
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    total_cost = Column(Float, default=0.0)
    total_latency_ms = Column(Float, default=0.0)
    graph_json = Column(Text, nullable=True)
    workflow_state_json = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    error = Column(Text, nullable=True)


class TraceModel(Base):
    """Execution trace per node (Composant F, section 7.6.1)."""

    __tablename__ = "traces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(String, nullable=False, index=True)
    node_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="completed")  # completed, skipped, failed
    model = Column(String, nullable=True)
    prompt_json = Column(Text, nullable=True)
    response_json = Column(Text, nullable=True)
    tokens_input = Column(Integer, default=0)
    tokens_output = Column(Integer, default=0)
    cost = Column(Float, default=0.0)
    latency_ms = Column(Float, default=0.0)
    decision_json = Column(Text, nullable=True)
    fallbacks_triggered_json = Column(Text, nullable=True)
    error = Column(Text, nullable=True)


class MetricModel(Base):
    """Evaluation Engine metrics persistence per execution."""

    __tablename__ = "metrics"

    id = Column(String, primary_key=True)  # execution_id
    execution_id = Column(String, nullable=False, index=True)
    cost_usd = Column(Float, default=0.0)
    latency_ms = Column(Float, default=0.0)
    quality_score = Column(Float, default=0.0)
    robustness_score = Column(Float, default=1.0)
    composite_score = Column(Float, default=0.0)
    report_json = Column(Text, nullable=False, default="{}")


class BenchmarkModel(Base):
    """Benchmark runs persistence (stub S6)."""

    __tablename__ = "benchmarks"

    id = Column(String, primary_key=True, index=True)
    status = Column(String, nullable=False, default="ACCEPTED")  # ACCEPTED, RUNNING, COMPLETED, FAILED
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    scenarios_json = Column(Text, nullable=False, default="[]")
    results_json = Column(Text, nullable=True)


def init_db() -> None:
    """Create all SQLite tables if they do not exist."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
