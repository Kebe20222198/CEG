"""Pydantic schemas for FastAPI REST endpoints."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field

from ceg.models.task import SubTask, TaskConstraint


# ── Task Schemas ──────────────────────────────────────────────────────────────

class TaskCreate(BaseModel):
    """Payload for creating a CognitiveTask."""

    id: str | None = Field(default=None, description="Optional custom ID for task.")
    name: str | None = Field(default=None, description="Human-readable name.")
    objective: str = Field(..., description="Primary objective of the task.")
    task_constraints: TaskConstraint | None = Field(default=None, description="Cost, latency, quality constraints.")
    tools_allowed: list[str] = Field(default_factory=list, description="Allowed tools.")
    subtasks: list[SubTask] = Field(default_factory=list, description="Subtasks composing the graph.")
    evaluation_criteria: list[dict[str, Any]] = Field(default_factory=list, description="Evaluation criteria list.")


class TaskUpdate(BaseModel):
    """Payload for updating a CognitiveTask."""

    name: str | None = None
    objective: str | None = None
    task_constraints: TaskConstraint | None = None
    tools_allowed: list[str] | None = None
    subtasks: list[SubTask] | None = None


class TaskResponse(BaseModel):
    """Schema for returning a CognitiveTask."""

    id: str
    name: str | None = None
    objective: str
    task_constraints: TaskConstraint | None = None
    tools_allowed: list[str] = Field(default_factory=list)
    subtasks: list[SubTask] = Field(default_factory=list)
    evaluation_criteria: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str


# ── Execution Schemas ─────────────────────────────────────────────────────────

class ExecuteTaskRequest(BaseModel):
    """Payload for executing a CognitiveTask."""

    scenario_name: str = Field(default="scenario_b", description="Scenario label (scenario_a, scenario_b, scenario_c, scenario_d).")
    csv_path: str | None = Field(default=None, description="Custom path to CSV input data file.")
    inputs: dict[str, Any] = Field(default_factory=dict, description="Additional runtime inputs.")


class ResumeExecutionRequest(BaseModel):
    """Payload for resuming a Human-in-the-Loop paused execution."""

    approved: bool = Field(default=True, description="Whether to approve or reject the paused action.")
    value: Any = Field(default=True, description="Value or dictionary passed to the resume handler.")
    comment: str | None = Field(default=None, description="Optional reviewer comment.")


class ExecutionResponse(BaseModel):
    """Summary schema for listing executions."""

    id: str
    task_id: str | None = None
    scenario_name: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    total_cost: float
    total_latency_ms: float
    summary: str | None = None
    error: str | None = None


class ExecutionDetailResponse(ExecutionResponse):
    """Detailed execution schema including graph topology and full workflow state."""

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
    """Evaluation Engine metrics for an execution."""

    execution_id: str
    cost_usd: float
    latency_ms: float
    quality_score: float
    robustness_score: float
    composite_score: float
    report: dict[str, Any]


# ── Benchmark Schemas (Stub S6) ───────────────────────────────────────────────

class BenchmarkRequest(BaseModel):
    """Request payload to trigger a benchmark run."""

    task_id: str | None = None
    n_runs: int = 10
    scenarios: list[str] = Field(default_factory=lambda: ["scenario_a", "scenario_b", "scenario_c", "scenario_d"])


class BenchmarkResponse(BaseModel):
    """Response returned upon launching benchmark (HTTP 202)."""

    id: str
    status: str
    message: str
    results: dict[str, Any] | None = None


# ── Model & Health Schemas ────────────────────────────────────────────────────

class ModelInfo(BaseModel):
    """Information on supported LLM models."""

    id: str
    name: str
    tier: str
    cost_per_1k_input: float
    cost_per_1k_output: float


class HealthResponse(BaseModel):
    """Health check status response."""

    status: str
    version: str
    sqlite: str
