"""CEGNode model definition for nodes in the Cognitive Execution Graph."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ceg.models.task import CognitiveTask


class NodeStatus(str, Enum):
    """Execution status of a CEG node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ModelTierHint(str, Enum):
    """Hint for LLM model selection tier."""

    FAST = "fast"
    BALANCED = "balanced"
    QUALITY = "quality"


class ExecutionRecord(BaseModel):
    """Record of a single node execution attempt."""

    attempt: int = Field(default=1, ge=1, description="Attempt number.")
    status: str = Field(default="pending", description="Execution status.")
    output: Any = Field(default=None, description="Output of the execution.")
    cost: float = Field(
        default=0.0, ge=0.0, description="Cost incurred during this execution."
    )
    latency_ms: float = Field(
        default=0.0, ge=0.0, description="Latency in milliseconds."
    )
    model_used: str | None = Field(
        default=None, description="Model used for this execution."
    )
    error: str | None = Field(
        default=None, description="Error message if execution failed."
    )


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
    status: NodeStatus = Field(
        default=NodeStatus.PENDING,
        description="Current execution status of the node.",
    )
    assigned_model: str | None = Field(
        default=None,
        description="LLM model assigned to execute this node.",
    )
    accumulated_cost: float = Field(
        default=0.0,
        ge=0.0,
        description="Accumulated financial or token cost incurred by this node.",
    )
    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Accumulated latency in milliseconds.",
    )
    confidence_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score (between 0.0 and 1.0) for node output.",
    )
    output: Any = Field(
        default=None,
        description="Output produced by this node after execution.",
    )
    execution_history: list[ExecutionRecord | dict[str, Any]] = Field(
        default_factory=list,
        description="Log of historical executions, step results, and metrics.",
    )
    required_capabilities: list[str] = Field(
        default_factory=list,
        description="Capabilities required to execute this node.",
    )
    model_tier_hint: ModelTierHint | str | None = Field(
        default=None,
        description="Hint for model selection tier (fast/balanced/quality).",
    )
    task: CognitiveTask | None = Field(
        default=None,
        description="Optional CognitiveTask associated with this node.",
    )
    interrupt_before: bool = Field(
        default=False,
        description=(
            "If True, the workflow pauses before executing this node, "
            "awaiting human approval. Requires a checkpointer."
        ),
    )
    interrupt_after: bool = Field(
        default=False,
        description=(
            "If True, the workflow pauses after executing this node, "
            "allowing human review of the output. Requires a checkpointer."
        ),
    )
    subgraph: Any | None = Field(
        default=None,
        description=(
            "Optional nested CEGGraph subgraph encapsulated within this node. "
            "The compiler compiles the subgraph and embeds it as a composite node."
        ),
    )

    @property
    def is_subgraph(self) -> bool:
        """Return True if this node encapsulates an inner CEGGraph subgraph."""
        return self.subgraph is not None
