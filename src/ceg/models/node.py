"""CEGNode model definition for nodes in the Cognitive Execution Graph."""

from typing import Any

from pydantic import BaseModel, Field

from ceg.models.task import CognitiveTask


class CEGNode(BaseModel):
    """Represents a node in the Cognitive Execution Graph (CEG).

    Each node tracks its objective, dependencies, accumulated cost,
    confidence score, and execution history.
    """

    id: str = Field(
        ...,
        description="Unique identifier for the node within the graph.",
        min_length=1,
    )
    objective: str = Field(
        ...,
        description="The target objective or purpose of this node.",
        min_length=1,
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="Node IDs that must complete before this node can execute.",
    )
    accumulated_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Accumulated financial or token cost incurred by this node.",
    )
    confidence_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score (between 0.0 and 1.0) for node output.",
    )
    execution_history: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Log of historical executions, step results, and metrics.",
    )
    task: CognitiveTask | None = Field(
        default=None,
        description="Optional CognitiveTask associated with this node.",
    )
