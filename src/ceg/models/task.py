"""CognitiveTask model definition for SDK layer."""

from typing import Any

from pydantic import BaseModel, Field


class CognitiveTask(BaseModel):
    """Cognitive task with objectives, constraints, and success criteria."""

    objective: str = Field(
        ...,
        description="The primary goal or instruction for the cognitive task.",
        min_length=1,
    )
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
