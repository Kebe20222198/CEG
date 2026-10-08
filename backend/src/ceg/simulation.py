"""Simulated LLM environment, to evaluate the optimiser without real API calls.

Each model has a *true* quality per capability, hidden from the optimiser.
The static registry ratings (``_CAPABILITY_RATINGS``) are its prior belief;
``SIMULATED_TRUTH`` deliberately differs from them, as real models differ
from what their documentation suggests:

  - ``fast-mini`` is much weaker than rated at analysis (anomaly detection,
    reasoning, trend analysis): it often produces an unusable answer;
  - ``fast-lite`` is better than rated at data and writing tasks;
  - ``balanced-standard`` is better than rated at anomaly detection and
    reasoning.

Every call draws a quality around the true value. Below ``failure_below``
the answer is unusable: the call fails (``ExecutionError``) and is billed
anyway. Draws are deterministic: they depend on (seed, node, model, call
number), never on thread scheduling, so a run is reproducible on any backend.

These numbers are simulation hypotheses, not measurements: they show that
the learned optimiser corrects a wrong prior, not how real models compare.
"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, replace
from typing import Any

from ceg.compiler.mock_executor import ExecutionError, ExecutionResult, MockExecutor
from ceg.models.graph import CEGGraph
from ceg.runtime.decision_engine import ModelProfile

# True quality per (model, capability) where it differs from the static rating.
SIMULATED_TRUTH: dict[str, dict[str, float]] = {
    "fast-mini": {
        "anomaly_detection": 0.30,
        "reasoning": 0.35,
        "trend_analysis": 0.40,
    },
    "fast-lite": {
        "data_retrieval": 0.92,
        "aggregation": 0.86,
        "computation": 0.86,
        "summarization": 0.88,
        "text_generation": 0.88,
    },
    "balanced-standard": {
        "anomaly_detection": 0.90,
        "reasoning": 0.88,
    },
}


@dataclass(frozen=True)
class SimulatedCall:
    """One call made to the simulated environment (ground truth)."""

    node_id: str
    model: str
    true_quality: float
    drawn_quality: float
    success: bool
    cost: float
    latency_ms: float


def true_quality(
    model: ModelProfile,
    capabilities: list[str],
    truth: dict[str, dict[str, float]] | None = None,
) -> float:
    """Hidden true quality of ``model`` on ``capabilities``."""
    overrides = (SIMULATED_TRUTH if truth is None else truth).get(model.name, {})
    if not capabilities:
        return model.quality_rating_for(capabilities)
    values = [
        overrides.get(cap, model.quality_rating_for([cap])) for cap in capabilities
    ]
    return sum(values) / len(values)


class SimulatedLLMExecutor(MockExecutor):
    """Executor whose call outcomes depend on the model's hidden true quality.

    Args:
        graph: The graph to run (gives each node's required capabilities,
            sub-graphs included).
        seed: Seed of the deterministic draws (use a different one per run).
        noise: Standard deviation of the drawn quality around the truth.
        failure_below: Drawn quality under which the answer is unusable.
        truth: Override of ``SIMULATED_TRUTH``.
    """

    def __init__(
        self,
        graph: CEGGraph,
        *,
        seed: int = 0,
        noise: float = 0.05,
        failure_below: float = 0.5,
        truth: dict[str, dict[str, float]] | None = None,
    ) -> None:
        super().__init__()
        self.seed = seed
        self.noise = noise
        self.failure_below = failure_below
        self.truth = truth
        self.capabilities = _capabilities_by_node(graph)
        self.calls: list[SimulatedCall] = []
        self._counts: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def execute(
        self,
        node_id: str,
        objective: str,
        inputs: dict[str, Any],
        attempt: int = 1,
        model: ModelProfile | None = None,
    ) -> ExecutionResult:
        """Run ``node_id`` on ``model``; fail if the drawn quality is too low."""
        result = super().execute(node_id, objective, inputs, attempt, model)
        if model is None:
            return result

        with self._lock:
            k = self._counts.get((node_id, model.name), 0)
            self._counts[(node_id, model.name)] = k + 1
        expected = true_quality(model, self.capabilities.get(node_id, []), self.truth)
        rng = random.Random(f"{self.seed}:{node_id}:{model.name}:{k}")
        drawn = min(1.0, max(0.0, rng.gauss(expected, self.noise)))
        success = drawn >= self.failure_below

        call = SimulatedCall(
            node_id=node_id,
            model=model.name,
            true_quality=round(expected, 4),
            drawn_quality=round(drawn, 4),
            success=success,
            cost=result.cost,
            latency_ms=result.latency_ms,
        )
        with self._lock:
            self.calls.append(call)

        if not success:
            raise ExecutionError(
                node_id,
                f"unusable answer from '{model.name}' (quality {drawn:.2f})",
                cost=result.cost,
                latency_ms=result.latency_ms,
            )
        output = {**result.output, "quality": round(drawn, 4)}
        return replace(result, output=output, confidence=drawn)


def _capabilities_by_node(graph: CEGGraph) -> dict[str, list[str]]:
    caps: dict[str, list[str]] = {}
    for node in graph.nodes:
        caps[node.id] = node.required_capabilities
        if node.subgraph is not None:
            caps.update(_capabilities_by_node(node.subgraph))
    return caps
