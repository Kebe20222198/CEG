"""Execution service: plans a task, runs it on a backend, stores the outcome.

Shared by the REST routers and the seed script, so that both go through the
same code path instead of the seed calling a route function directly.

The stored task is a declaration: it is turned into a CognitiveTask, planned
(``ceg.planner.plan``) and compiled on the requested backend, which enforces
its constraints. A declaration or a backend that cannot honour them is
refused before anything runs.

Every run uses the execution id as thread id; on a backend supporting it, a
checkpointer is attached. This gives two things:
  - Human-in-the-Loop: a run that reaches an approval node stops with status
    ``awaiting_approval`` and ``resume_execution`` continues the same run;
  - honest failure traces: when a node aborts, the nodes that completed
    before it are read back from the last checkpoint.

The checkpointer lives in memory: paused executions do not survive a server
restart (resuming one then fails with a clear error).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.orm import Session

from api.db import ExecutionModel, MetricModel, TaskModel, TraceModel
from api.pipelines import PIPELINES, PipelineSpec
from api.schemas import (
    ExecuteTaskRequest,
    ExecutionDetailResponse,
    ExecutionResponse,
    ResumeExecutionRequest,
)
from ceg.backends import DEFAULT_BACKEND, Backend, Workflow, get_backend
from ceg.compiler.mock_executor import MockExecutor
from ceg.evaluation.engine import EvaluationEngine
from ceg.evaluation.models import Criterion, EvaluationReport
from ceg.models.graph import CEGGraph
from ceg.models.task import CognitiveTask, SubTask, TaskConstraint
from ceg.planner import PlanningError, plan
from ceg.runtime.decision_engine import RuntimeDecisionEngine
from ceg.runtime.fallback import NodeAbortError
from ceg.runtime.statistics import ModelStatistics

FIXTURES_DIR = (Path(__file__).parent.parent / "tests" / "fixtures").resolve()

# Scenario name keywords → bundled sample CSV (first match wins).
_SCENARIO_FIXTURES: list[tuple[tuple[str, ...], str]] = [
    (("scenario_a", "normal"), "scenario_a_normal.csv"),
    (("scenario_c", "multi"), "scenario_c_multiple_anomalies.csv"),
    (("scenario_d", "corrupted", "fault"), "scenario_d_corrupted.csv"),
]
_DEFAULT_FIXTURE = "scenario_b_single_anomaly.csv"

CHECKPOINTER = MemorySaver()

# Learned optimiser: statistics shared by every 'learned' execution, saved to
# a JSON file so that what was learned survives a restart.
STATISTICS_PATH = Path(
    os.getenv(
        "CEG_STATS_PATH",
        str(Path(__file__).parent.parent / "model_statistics.json"),
    )
)


def _load_statistics() -> ModelStatistics:
    if STATISTICS_PATH.is_file():
        return ModelStatistics.from_dict(json.loads(STATISTICS_PATH.read_text()))
    return ModelStatistics(exploration=0.1)


LEARNED_STATISTICS = _load_statistics()


def _engine_for(optimizer: str | None) -> RuntimeDecisionEngine:
    """Engine using the learned statistics, or the static ratings."""
    statistics = LEARNED_STATISTICS if optimizer == "learned" else None
    return RuntimeDecisionEngine(statistics=statistics)


def save_statistics() -> None:
    """Persist what the learned optimiser knows."""
    STATISTICS_PATH.write_text(json.dumps(LEARNED_STATISTICS.to_dict(), indent=2))


class ExecutionRequestError(ValueError):
    """The request cannot be executed as given (mapped to HTTP 400)."""


class ExecutionStateError(RuntimeError):
    """The execution is not in a state allowing this action (HTTP 409)."""


# ── Helpers: inputs ───────────────────────────────────────────────────────────


def allowed_data_dirs() -> list[Path]:
    """Directories a client-supplied CSV path may point into.

    The bundled fixtures, plus ``CEG_DATA_DIR`` when set. Any other path is
    refused: the API must not read arbitrary files of the server.
    """
    dirs = [FIXTURES_DIR]
    extra = os.getenv("CEG_DATA_DIR")
    if extra:
        dirs.append(Path(extra).resolve())
    return dirs


def resolve_csv_path(payload: ExecuteTaskRequest) -> str:
    """Return the CSV to read: the client's path if allowed, else a fixture."""
    if payload.csv_path:
        candidate = Path(payload.csv_path).resolve()
        if not any(candidate.is_relative_to(d) for d in allowed_data_dirs()):
            raise ExecutionRequestError(
                "csv_path must point into the sample data directory or into "
                "CEG_DATA_DIR."
            )
        if not candidate.is_file():
            raise ExecutionRequestError(f"CSV file not found: {payload.csv_path}")
        return str(candidate)

    scenario = payload.scenario_name.lower()
    for keywords, filename in _SCENARIO_FIXTURES:
        if any(k in scenario for k in keywords):
            return str(FIXTURES_DIR / filename)
    return str(FIXTURES_DIR / _DEFAULT_FIXTURE)


# ── Helpers: task → runnable pieces ───────────────────────────────────────────


def _pipeline_of(task: TaskModel) -> PipelineSpec | None:
    if task.pipeline is None:
        return None
    spec = PIPELINES.get(task.pipeline)
    if spec is None:
        raise ExecutionRequestError(f"Unknown pipeline '{task.pipeline}'.")
    return spec


def cognitive_task_of(task: TaskModel) -> CognitiveTask:
    """The declaration stored for ``task``.

    A task based on a pipeline template and declaring no sub-tasks of its
    own uses the template's sub-tasks and tools; its constraints are its own
    if set, else the template's. A task without any declared constraints
    gets the defaults: every task the API runs has a budget and a latency
    limit.
    """
    spec = PIPELINES.get(task.pipeline) if task.pipeline else None
    template = spec.declare() if spec is not None else None

    subtasks = [
        SubTask.model_validate(st) for st in json.loads(task.subtasks_json or "[]")
    ]
    tools_allowed: list[str] = json.loads(task.tools_allowed_json or "[]")
    if not subtasks and template is not None:
        subtasks = template.subtasks
        tools_allowed = template.tools_allowed

    if task.task_constraints_json:
        constraints = TaskConstraint.model_validate_json(task.task_constraints_json)
    elif template is not None and template.task_constraints is not None:
        constraints = template.task_constraints
    else:
        constraints = TaskConstraint()

    return CognitiveTask(
        name=task.name,
        objective=task.objective,
        task_constraints=constraints,
        tools_allowed=tools_allowed,
        subtasks=subtasks,
    )


def _criteria_of(task: TaskModel, spec: PipelineSpec | None) -> list[Criterion]:
    if spec is not None and spec.criteria:
        return list(spec.criteria)
    raw = json.loads(task.evaluation_criteria_json or "[]")
    return [Criterion.model_validate(c) for c in raw]


def _executor_of(spec: PipelineSpec | None) -> MockExecutor:
    return spec.make_executor() if spec is not None else MockExecutor()


def _backend(name: str) -> Backend:
    try:
        return get_backend(name)
    except ValueError as exc:
        raise ExecutionRequestError(str(exc)) from exc


def _compile(
    backend: Backend,
    graph: CEGGraph,
    executor: MockExecutor,
    engine: RuntimeDecisionEngine,
) -> Workflow:
    """Compile ``graph``; the backend enforces the task's constraints."""
    return backend.compile(
        graph,
        engine=engine,
        executor=executor,
        checkpointer=CHECKPOINTER if backend.supports_hitl else None,
    )


# ── Public API ────────────────────────────────────────────────────────────────


def start_execution(
    db: Session, task: TaskModel, payload: ExecuteTaskRequest
) -> ExecutionModel:
    """Execute ``task`` and persist the outcome.

    Returns the execution record, whose status is ``completed``, ``failed``
    or ``awaiting_approval``.

    Raises:
        ExecutionRequestError: If the request is invalid (nothing is stored).
    """
    spec = _pipeline_of(task)
    declaration = cognitive_task_of(task)
    try:
        graph = plan(declaration)
    except PlanningError as exc:
        raise ExecutionRequestError(f"Invalid task declaration: {exc}") from exc
    criteria = _criteria_of(task, spec)

    inputs = dict(payload.inputs)
    if spec is not None and spec.uses_csv:
        inputs["csv_path"] = resolve_csv_path(payload)
    elif payload.csv_path:
        raise ExecutionRequestError("This task's pipeline does not read a CSV file.")

    # Compiling checks the plan against the backend and the constraints:
    # a request that cannot be honoured is refused before anything is stored.
    backend = _backend(payload.backend)
    engine = _engine_for(payload.optimizer)
    try:
        workflow = _compile(backend, graph, _executor_of(spec), engine)
    except ValueError as exc:
        raise ExecutionRequestError(str(exc)) from exc

    record = ExecutionModel(
        id=f"exec_{uuid.uuid4().hex[:12]}",
        task_id=task.id,
        scenario_name=payload.scenario_name,
        backend=backend.name,
        optimizer=payload.optimizer,
        status="running",
        started_at=_now(),
        graph_json=graph.model_dump_json(),
    )
    db.add(record)
    db.commit()

    try:
        state, error = _invoke(
            workflow,
            lambda: workflow.invoke({"inputs": inputs}, thread_id=record.id),
            record.id,
        )
        _store_outcome(
            db,
            record,
            graph,
            state,
            error,
            workflow,
            evaluation=_Evaluation(
                criteria=criteria,
                declaration=declaration,
                robustness_runs=payload.robustness_runs,
                spec=spec,
                inputs=inputs,
                backend=backend.name,
            ),
        )
    except Exception as exc:
        _mark_failed(db, record, f"Internal error: {exc}")
        raise
    return record


def resume_execution(
    db: Session, record: ExecutionModel, payload: ResumeExecutionRequest
) -> ExecutionModel:
    """Continue an execution paused on a Human-in-the-Loop node.

    Raises:
        ExecutionStateError: If the execution is not awaiting approval, or its
            checkpoint is gone (server restarted since the pause).
    """
    if record.status != "awaiting_approval":
        raise ExecutionStateError(
            f"Execution '{record.id}' is '{record.status}', not awaiting approval."
        )
    task = db.get(TaskModel, record.task_id) if record.task_id else None
    if task is None:
        raise ExecutionStateError("The task of this execution no longer exists.")

    backend = get_backend(record.backend or DEFAULT_BACKEND)
    if not backend.supports_hitl:
        raise ExecutionStateError(
            f"The '{backend.name}' backend cannot resume an execution."
        )
    spec = _pipeline_of(task)
    # The plan the execution started with, with its task and constraints.
    graph = CEGGraph.model_validate_json(record.graph_json or "{}")
    engine = _engine_for(record.optimizer)
    workflow = _compile(backend, graph, _executor_of(spec), engine)

    snapshot = workflow.get_state(record.id)
    if not snapshot.next:
        _mark_failed(
            db,
            record,
            "Checkpoint lost (the API was restarted while the execution was "
            "paused): it cannot be resumed.",
        )
        raise ExecutionStateError(record.error or "")

    # The budget already spent before the pause still counts.
    engine.spend(float(snapshot.values.get("total_cost", 0.0)))

    decision: dict[str, Any] = {**(payload.value or {}), "approved": payload.approved}
    if payload.comment:
        decision["comment"] = payload.comment

    try:
        state, error = _invoke(
            workflow, lambda: workflow.resume(record.id, value=decision), record.id
        )
        _store_outcome(
            db,
            record,
            graph,
            state,
            error,
            workflow,
            evaluation=_Evaluation(
                criteria=_criteria_of(task, spec),
                declaration=graph.task or cognitive_task_of(task),
                robustness_runs=0,
                spec=spec,
                inputs={},
                backend=backend.name,
            ),
        )
    except Exception as exc:
        _mark_failed(db, record, f"Internal error: {exc}")
        raise
    return record


def to_response(record: ExecutionModel) -> ExecutionResponse:
    """Summary view of an execution record."""
    return ExecutionResponse(**_summary_fields(record))


def to_detail(record: ExecutionModel) -> ExecutionDetailResponse:
    """Detailed view of an execution record (graph and workflow state)."""
    return ExecutionDetailResponse(
        **_summary_fields(record),
        graph=json.loads(record.graph_json) if record.graph_json else None,
        workflow_state=(
            json.loads(record.workflow_state_json)
            if record.workflow_state_json
            else None
        ),
    )


# ── Internals ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Evaluation:
    """What the Evaluation Engine needs once a run is over."""

    criteria: list[Criterion]
    declaration: CognitiveTask
    robustness_runs: int
    spec: PipelineSpec | None
    inputs: dict[str, Any]
    backend: str

    @property
    def constraints(self) -> TaskConstraint:
        return self.declaration.task_constraints or TaskConstraint()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _invoke(
    workflow: Workflow,
    run: Callable[[], dict[str, Any]],
    thread_id: str,
) -> tuple[dict[str, Any], NodeAbortError | None]:
    """Run ``run()``; on abort, return the state saved at the last checkpoint."""
    try:
        return run(), None
    except NodeAbortError as exc:
        partial = dict(workflow.get_state(thread_id).values)
        return partial, exc


def _failure_entry(error: NodeAbortError) -> dict[str, Any]:
    """Trace entry for the node that aborted, from the abort error itself."""
    return {
        "node_id": error.node_id,
        "status": "failed",
        "output": None,
        "cost": 0.0,
        "latency_ms": 0.0,
        "confidence": 0.0,
        "model_used": None,
        "fallbacks_triggered": error.fallbacks_triggered,
        "error": error.reason,
    }


def _store_outcome(
    db: Session,
    record: ExecutionModel,
    graph: CEGGraph,
    state: dict[str, Any],
    error: NodeAbortError | None,
    workflow: Workflow,
    evaluation: _Evaluation,
) -> None:
    """Persist state, traces and (once the run is over) metrics.

    A run that completed but does not meet its declared constraints (e.g. a
    quality below ``min_quality_score``) is marked ``failed``.
    """
    state = dict(state)
    interrupts = state.pop("__interrupt__", None)
    log: list[dict[str, Any]] = list(state.get("execution_log", []))

    if error is not None:
        log.append(_failure_entry(error))
        state["execution_log"] = log
        status = "failed"
        record.error = str(error)
    elif interrupts:
        status = "awaiting_approval"
        state["pending_approvals"] = [i.value for i in interrupts]
    else:
        status = "completed"
        record.error = None

    report: EvaluationReport | None = None
    if status != "awaiting_approval":
        report = _evaluate(record, state, evaluation)
        if status == "completed" and report.constraint_violations:
            status = "failed"
            record.error = "Declared constraints not met: " + "; ".join(
                report.constraint_violations
            )
        if status == "failed":
            # A failed run produced no usable result: its speed and cost
            # are no merit.
            report.composite_score = 0.0
            report.metadata["failed"] = True

    total_cost = float(state.get("total_cost", 0.0))
    total_latency_ms = float(state.get("total_latency_ms", 0.0))
    record.status = status
    record.total_cost = total_cost
    record.total_latency_ms = total_latency_ms
    record.workflow_state_json = json.dumps(state, default=str)
    record.summary = (
        f"Scénario {record.scenario_name} ({evaluation.backend}): {status}. "
        f"Coût: ${total_cost:.4f}, Latence: {total_latency_ms:.1f}ms."
    )

    _replace_traces(db, record.id, graph, log)

    if report is not None:
        record.completed_at = _now()
        _store_metrics(db, record, report)
        workflow.discard(record.id)
    if record.optimizer == "learned":
        save_statistics()

    db.commit()


def _replace_traces(
    db: Session, execution_id: str, graph: CEGGraph, log: list[dict[str, Any]]
) -> None:
    objectives = {node.id: node.objective for node in graph.nodes}
    db.query(TraceModel).filter(TraceModel.execution_id == execution_id).delete()
    for entry in log:
        node_id = entry.get("node_id", "unknown")
        objective = objectives.get(node_id)
        db.add(
            TraceModel(
                execution_id=execution_id,
                node_id=node_id,
                status=entry.get("status", "completed"),
                model=entry.get("model_used"),
                prompt_json=json.dumps({"objective": objective}) if objective else None,
                response_json=json.dumps(entry.get("output"), default=str),
                # Token counts are not simulated by the mock executors.
                tokens_input=0,
                tokens_output=0,
                cost=float(entry.get("cost", 0.0)),
                latency_ms=float(entry.get("latency_ms", 0.0)),
                decision_json=json.dumps(entry.get("decision"), default=str),
                fallbacks_triggered_json=json.dumps(entry.get("fallbacks_triggered")),
                error=entry.get("error"),
            )
        )


def _evaluate(
    record: ExecutionModel, state: dict[str, Any], evaluation: _Evaluation
) -> EvaluationReport:
    engine = EvaluationEngine(criteria=evaluation.criteria)

    robustness: float | None = None
    if evaluation.robustness_runs > 0:
        spec = evaluation.spec
        robustness = engine.measure_robustness(
            build_fn=lambda: plan(evaluation.declaration),
            executor_factory=spec.make_executor if spec else MockExecutor,
            scenario_name=record.scenario_name,
            n_runs=evaluation.robustness_runs,
            inputs=evaluation.inputs,
            backend=evaluation.backend,
        ).success_rate

    return engine.evaluate(
        workflow_state=state,
        scenario_name=record.scenario_name,
        robustness_score=robustness,
        constraints=evaluation.constraints,
    )


def _store_metrics(
    db: Session, record: ExecutionModel, report: EvaluationReport
) -> None:
    db.query(MetricModel).filter(MetricModel.execution_id == record.id).delete()
    db.add(
        MetricModel(
            id=record.id,
            execution_id=record.id,
            cost_usd=report.total_cost_usd,
            latency_ms=report.total_latency_ms,
            quality_score=report.quality_score,
            robustness_score=report.robustness_score,
            composite_score=report.composite_score,
            report_json=report.model_dump_json(),
        )
    )


def _mark_failed(db: Session, record: ExecutionModel, message: str) -> None:
    """Never leave an execution stuck in ``running`` after an internal error."""
    db.rollback()
    record.status = "failed"
    record.error = message
    record.completed_at = _now()
    db.commit()


def _summary_fields(record: ExecutionModel) -> dict[str, Any]:
    return {
        "id": record.id,
        "task_id": record.task_id,
        "scenario_name": record.scenario_name,
        "backend": record.backend,
        "optimizer": record.optimizer,
        "status": record.status,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": (
            record.completed_at.isoformat() if record.completed_at else None
        ),
        "total_cost": record.total_cost,
        "total_latency_ms": record.total_latency_ms,
        "summary": record.summary,
        "error": record.error,
    }
