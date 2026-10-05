"""Pydantic schemas for FastAPI REST endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ceg.evaluation.models import Criterion
from ceg.models.task import SubTask, TaskConstraint

# ── Task Schemas ──────────────────────────────────────────────────────────────


class TaskCreate(BaseModel):
    """Payload for creating a CognitiveTask."""

    id: str | None = Field(default=None, description="Optional custom ID for task.")
    name: str | None = Field(default=None, description="Human-readable name.")
    objective: str = Field(..., description="Primary objective of the task.")
    pipeline: str | None = Field(
        default=None,
        description=(
            "Registered pipeline template to execute (see GET /pipelines). "
            "If omitted, the graph is built from the subtasks."
        ),
    )
    task_constraints: TaskConstraint | None = Field(
        default=None, description="Cost, latency, quality constraints."
    )
    tools_allowed: list[str] = Field(default_factory=list, description="Allowed tools.")
    subtasks: list[SubTask] = Field(
        default_factory=list, description="Subtasks composing the graph."
    )
    evaluation_criteria: list[Criterion] = Field(
        default_factory=list, description="Quality criteria for the LLM judge."
    )


class TaskUpdate(BaseModel):
    """Payload for updating a CognitiveTask (omitted fields are unchanged)."""

    name: str | None = None
    objective: str | None = None
    pipeline: str | None = None
    task_constraints: TaskConstraint | None = None
    tools_allowed: list[str] | None = None
    subtasks: list[SubTask] | None = None
    evaluation_criteria: list[Criterion] | None = None


class TaskResponse(BaseModel):
    """Schema for returning a CognitiveTask."""

    id: str
    name: str | None = None
    objective: str
    pipeline: str | None = None
    task_constraints: TaskConstraint | None = None
    tools_allowed: list[str] = Field(default_factory=list)
    subtasks: list[SubTask] = Field(default_factory=list)
    evaluation_criteria: list[Criterion] = Field(default_factory=list)
    created_at: str
    updated_at: str


# ── Execution Schemas ─────────────────────────────────────────────────────────


class ExecuteTaskRequest(BaseModel):
    """Payload for executing a CognitiveTask."""

    scenario_name: str = Field(
        default="scenario_b",
        description=(
            "Scenario label. For CSV pipelines it also picks the sample file "
            "(scenario_a, scenario_b, scenario_c, scenario_d) when csv_path "
            "is not given."
        ),
    )
    csv_path: str | None = Field(
        default=None,
        description=(
            "CSV input file, for CSV pipelines only. Must lie in the sample "
            "data directory or in CEG_DATA_DIR."
        ),
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Graph inputs, available to every node executor.",
    )
    backend: str = Field(
        default="langgraph",
        description=(
            "Execution backend (see GET /backends). The same task gives the "
            "same results on every backend; human approval needs one that "
            "supports it."
        ),
    )
    robustness_runs: int = Field(
        default=0,
        ge=0,
        le=50,
        description=(
            "Number of extra runs used to measure robustness (pipelines only). "
            "0 leaves robustness unmeasured."
        ),
    )


class ResumeExecutionRequest(BaseModel):
    """Payload for resuming a Human-in-the-Loop paused execution."""

    approved: bool = Field(
        default=True, description="Whether to approve or reject the paused action."
    )
    value: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Extra fields for the paused node, e.g. "
            '{"modified_output": {...}} to replace a reviewed output.'
        ),
    )
    comment: str | None = Field(default=None, description="Optional reviewer comment.")


class ExecutionResponse(BaseModel):
    """Summary schema for listing executions.

    status: running, awaiting_approval, completed or failed.
    """

    id: str
    task_id: str | None = None
    scenario_name: str
    backend: str | None = None
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    total_cost: float
    total_latency_ms: float
    summary: str | None = None
    error: str | None = None


class ExecutionDetailResponse(ExecutionResponse):
    """Detailed execution schema including graph topology and full workflow state.

    While paused, ``workflow_state["pending_approvals"]`` lists what awaits
    a decision.
    """

    graph: dict[str, Any] | None = None
    workflow_state: dict[str, Any] | None = None


# ── Trace Schemas ─────────────────────────────────────────────────────────────


class TraceItemResponse(BaseModel):
    """Trace details for a single node execution."""

    node_id: str
    status: str
    model: str | None = None
    prompt: Any = None
    response: Any = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost: float = 0.0
    latency_ms: float = 0.0
    decision: Any = None
    fallbacks_triggered: Any = None
    error: str | None = None


# ── Metric Schemas ────────────────────────────────────────────────────────────


class MetricsResponse(BaseModel):
    """Evaluation Engine metrics for an execution (None = not measured)."""

    execution_id: str
    cost_usd: float
    latency_ms: float
    quality_score: float | None
    robustness_score: float | None
    composite_score: float
    report: dict[str, Any]


# ── Benchmark Schemas (Stub S6) ───────────────────────────────────────────────


class BenchmarkRequest(BaseModel):
    """Request payload to trigger a benchmark run."""

    task_id: str | None = None
    n_runs: int = 10
    scenarios: list[str] = Field(
        default_factory=lambda: ["scenario_a", "scenario_b", "scenario_c", "scenario_d"]
    )


class BenchmarkResponse(BaseModel):
    """Response returned upon launching benchmark (HTTP 202)."""

    id: str
    status: str
    message: str
    results: dict[str, Any] | None = None


# ── Model, Pipeline & Health Schemas ──────────────────────────────────────────


class ModelInfo(BaseModel):
    """A model of the Runtime Decision Engine registry (simulated models)."""

    id: str
    tier: str
    estimated_cost: float
    estimated_latency_ms: float
    supported_capabilities: list[str]


class BackendInfo(BaseModel):
    """A registered execution backend."""

    id: str
    supports_hitl: bool


class PipelineInfo(BaseModel):
    """A registered pipeline template."""

    id: str
    uses_csv: bool
    criteria: list[str]


class HealthResponse(BaseModel):
    """Health check status response."""

    status: str
    version: str
    sqlite: str
