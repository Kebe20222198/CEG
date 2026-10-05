"""MockExecutor — simulates cognitive task execution without real LLM API calls.

Used during development and testing to provide deterministic, cost-free
execution of CEG nodes. Wired into the Runtime Decision Engine in S3.

S3 additions:
  - ``ExecutionError`` exception for controlled failure simulation
  - ``failure_nodes`` / ``max_failures`` to test fallback strategies
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ── Exceptions ────────────────────────────────────────────────────────────────


class ExecutionError(Exception):
    """Raised by MockExecutor to simulate a node execution failure.

    Args:
        node_id: The node that failed.
        reason: Human-readable failure description.
    """

    def __init__(self, node_id: str, reason: str = "Simulated failure") -> None:
        self.node_id = node_id
        self.reason = reason
        super().__init__(f"Execution failed for node '{node_id}': {reason}")


# ── Result ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExecutionResult:
    """Result of a single node execution."""

    output: Any
    cost: float
    latency_ms: float
    confidence: float


# ── Executor ──────────────────────────────────────────────────────────────────


class MockExecutor:
    """Fake executor that simulates cognitive task execution.

    Produces deterministic outputs based on ``node_id`` and ``objective``,
    with simulated cost and latency values.  No real LLM calls are made.

    S3 Failure Simulation:
        Pass ``failure_nodes`` to force failures on specific nodes.  Each node
        listed will raise ``ExecutionError`` on its first ``max_failures[node_id]``
        calls (default = 1), then succeed on subsequent calls.  This lets tests
        verify that Retry and Escalation strategies work correctly.

    Args:
        base_cost: Cost per character of the objective string.
        base_latency_ms: Base latency in milliseconds.
        default_confidence: Confidence score returned on success.
        failure_nodes: Set of node IDs that should fail (at least once).
        max_failures: Per-node failure count limit (key = node_id,
            value = number of failures before auto-success). Defaults to 1.
    """

    def __init__(
        self,
        base_cost: float = 0.001,
        base_latency_ms: float = 50.0,
        default_confidence: float = 0.85,
        failure_nodes: set[str] | None = None,
        max_failures: dict[str, int] | None = None,
    ) -> None:
        self.base_cost = base_cost
        self.base_latency_ms = base_latency_ms
        self.default_confidence = default_confidence
        self.failure_nodes: set[str] = failure_nodes or set()
        self.max_failures: dict[str, int] = max_failures or {}
        # Internal counter: how many times each node has already failed
        self._failure_count: dict[str, int] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def execute(
        self,
        node_id: str,
        objective: str,
        inputs: dict[str, Any],
        attempt: int = 1,
    ) -> ExecutionResult:
        """Simulate execution of a cognitive task node.

        Output, cost, and latency are deterministically derived from
        the ``node_id`` and ``objective`` so that tests produce reproducible
        results.

        Args:
            node_id: Unique identifier of the node being executed.
            objective: The node's objective / instruction text.
            inputs: Outputs from upstream nodes available as context.
            attempt: Current attempt number (tracked in output for traceability).

        Returns:
            ExecutionResult with simulated output, cost, latency, and confidence.

        Raises:
            ExecutionError: If this node is configured to fail and has not yet
                reached its ``max_failures`` limit.
        """
        if self._should_fail(node_id):
            self._failure_count[node_id] = self._failure_count.get(node_id, 0) + 1
            raise ExecutionError(
                node_id=node_id,
                reason=(
                    f"Simulated failure #{self._failure_count[node_id]} "
                    f"(max_failures={self.max_failures.get(node_id, 1)})"
                ),
            )

        output: dict[str, Any] = {
            "node_id": node_id,
            "result": f"Mock result for '{objective}'",
            "input_keys": sorted(inputs.keys()),
            "attempt": attempt,
        }

        cost = round(self.base_cost * len(objective), 6)
        latency = round(self.base_latency_ms + len(node_id) * 10.0, 2)

        return ExecutionResult(
            output=output,
            cost=cost,
            latency_ms=latency,
            confidence=self.default_confidence,
        )

    def reset_failures(self, node_id: str | None = None) -> None:
        """Reset the failure counter for one or all nodes.

        Args:
            node_id: If given, reset only that node's counter.
                     If ``None``, reset all counters.
        """
        if node_id is None:
            self._failure_count.clear()
        else:
            self._failure_count.pop(node_id, None)

    def add_failure_node(self, node_id: str, max_failures: int = 1) -> None:
        """Register a node as a failure node at runtime.

        Args:
            node_id: The node to configure for failure.
            max_failures: Number of times this node should fail before succeeding.
        """
        self.failure_nodes.add(node_id)
        self.max_failures[node_id] = max_failures

    # ── Private helpers ───────────────────────────────────────────────────────

    def _should_fail(self, node_id: str) -> bool:
        """Return True if this node should raise ExecutionError on this call."""
        if node_id not in self.failure_nodes:
            return False
        current_failures = self._failure_count.get(node_id, 0)
        limit = self.max_failures.get(node_id, 1)
        return current_failures < limit
