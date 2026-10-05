"""CognitiveTask: the declarative layer of CEG.

A CognitiveTask states *what* has to be achieved and *under which
constraints*, never *how*: the planner (``ceg.planner.plan``) turns it into
an execution graph, the Runtime Decision Engine picks the models, and a
backend (LangGraph, plain Python, ...) runs it — the way a SQL query is
planned, optimised and executed by a database engine.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskConstraint(BaseModel):
    """Constraints a CognitiveTask declares, and how CEG honours them.

    Attributes:
        max_cost_usd: Maximum total cost in USD. Guaranteed at run time: it is
            the Runtime Decision Engine's budget, never exceeded (a node that
            no longer fits is degraded or aborted).
        max_latency_seconds: Maximum cumulated latency of the node executions.
            Guaranteed at run time the same way as the budget.
        min_quality_score: Minimum acceptable quality score [0, 1]. Quality is
            only known once the result is judged: the Evaluation Engine checks
            it and reports a violation (or "unverified" when not measured).
    """

    max_cost_usd: float = Field(default=0.50, ge=0.0)
    max_latency_seconds: float = Field(default=15.0, ge=0.0)
    min_quality_score: float = Field(default=0.85, ge=0.0, le=1.0)


class RepeatSpec(BaseModel):
    """Repeat part of the work while a sub-task asks for it.

    After the sub-task carrying this spec has run, execution goes back to
    ``back_to`` while the sub-task's output has a truthy ``while_key``, at
    most ``max_iterations`` times.
    """

    back_to: str = Field(..., min_length=1, description="Sub-task to restart from.")
    while_key: str = Field(
        ..., min_length=1, description="Output key that requests another round."
    )
    max_iterations: int = Field(..., ge=1, description="Upper bound on repetitions.")


class SubTask(BaseModel):
    """A single sub-task within a CognitiveTask pipeline.

    Everything here is declarative: the planner decides the edges, the
    parallelism (independent sub-tasks run concurrently) and the routing.

    Attributes:
        id: Unique identifier within the parent CognitiveTask.
        objective: What this sub-task must accomplish.
        required_capabilities: Capabilities the selected model must support.
        model_tier_hint: Optional preferred model tier (fast/balanced/quality).
        dependencies: IDs of sub-tasks that must complete first.
        run_if: ``"<subtask_id>.<output_key>"`` — run only if that sub-task's
            output has a truthy key; otherwise this sub-task is skipped. The
            referenced sub-task becomes a dependency.
        repeat: Optional repetition, see ``RepeatSpec``.
        requires_approval: A human must approve before this sub-task runs.
        review_output: A human reviews (and may edit) the output afterwards.
        tools: Tools this sub-task uses; each must be in the task's
            ``tools_allowed``, otherwise the task is refused.
        subtasks: Nested sub-tasks: this sub-task is then a team whose work
            is planned as a sub-graph.
    """

    id: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    required_capabilities: list[str] = Field(default_factory=list)
    model_tier_hint: str | None = Field(default=None)
    dependencies: list[str] = Field(default_factory=list)
    run_if: str | None = Field(default=None)
    repeat: RepeatSpec | None = Field(default=None)
    requires_approval: bool = Field(default=False)
    review_output: bool = Field(default=False)
    tools: list[str] = Field(default_factory=list)
    subtasks: list[SubTask] = Field(default_factory=list)


class CognitiveTask(BaseModel):
    """Cognitive task with objectives, constraints, and success criteria.

    Backward-compatible with S1/S2/S3 usage (constraints as list[str]).
    S4 adds structured TaskConstraint, SubTask list, and SDK decorator support.
    """

    objective: str = Field(
        ...,
        description="The primary goal or instruction for the cognitive task.",
        min_length=1,
    )
    # S1/S2/S3 legacy field — kept for backward compatibility
    constraints: list[str] = Field(
        default_factory=list,
        description="List of boundaries or rules the execution must comply with.",
    )
    success_criteria: list[str] = Field(
        default_factory=list,
        description="List of conditions that must be met to declare task success.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary additional context or metadata.",
    )
    # S4 SDK fields
    name: str | None = Field(
        default=None,
        description="Optional human-readable name for this task.",
    )
    task_constraints: TaskConstraint | None = Field(
        default=None,
        description="Structured execution constraints (cost, latency, quality).",
    )
    tools_allowed: list[str] = Field(
        default_factory=list,
        description=(
            "Tools the sub-tasks may use. A sub-task declaring any other tool "
            "makes the task invalid."
        ),
    )
    subtasks: list[SubTask] = Field(
        default_factory=list,
        description="Ordered list of sub-tasks composing this cognitive pipeline.",
    )
    # S5 Evaluation Engine fields
    evaluation_criteria: list[Any] = Field(
        default_factory=list,
        description=(
            "List of Criterion objects defining quality evaluation dimensions. "
            "Type: list[ceg.evaluation.models.Criterion] — typed as Any to avoid "
            "circular imports between models and evaluation packages."
        ),
    )
