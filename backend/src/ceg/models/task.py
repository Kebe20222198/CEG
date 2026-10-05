"""CognitiveTask model definition for SDK layer."""

from typing import Any

from pydantic import BaseModel, Field


class TaskConstraint(BaseModel):
    """Hard execution constraints for a CognitiveTask.

    Attributes:
        max_cost_usd: Maximum total cost in USD for the task.
        max_latency_seconds: Maximum allowed wall-clock latency in seconds.
        min_quality_score: Minimum acceptable quality/confidence score [0, 1].
    """

    max_cost_usd: float = Field(default=0.50, ge=0.0)
    max_latency_seconds: float = Field(default=15.0, ge=0.0)
    min_quality_score: float = Field(default=0.85, ge=0.0, le=1.0)


class SubTask(BaseModel):
    """A single sub-task within a CognitiveTask pipeline.

    Attributes:
        id: Unique identifier within the parent CognitiveTask.
        objective: What this sub-task must accomplish.
        required_capabilities: Capabilities the executor must support.
        model_tier_hint: Optional hint for model tier selection.
        dependencies: IDs of sub-tasks that must complete first.
    """

    id: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    required_capabilities: list[str] = Field(default_factory=list)
    model_tier_hint: str | None = Field(default=None)
    dependencies: list[str] = Field(default_factory=list)


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
        description="List of tool names available for task execution.",
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
