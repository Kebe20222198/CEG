"""Runtime Decision Engine — dynamic model selection and execution orchestration.

This module is the heart of the CEG runtime layer.  For every node in the graph
it:
  1. Selects the optimal model given node requirements and current constraints.
  2. Delegates execution to the MockExecutor (no real LLM calls).
  3. Tracks the global budget transversally across all nodes.
  4. Triggers the FallbackOrchestrator on failure.

Key public API:
  - ``ModelProfile``       — description of a (mock) LLM model
  - ``Constraint``         — hard execution constraints (budget, latency)
  - ``SelectionWeights``   — scoring weights (w1/w2/w3, sum=1.0)
  - ``select_model()``     — pure selection function (spec-compliant algorithm)
  - ``RuntimeDecisionEngine`` — stateful orchestrator injected into the compiler
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from ceg.models.node import CEGNode, ModelTierHint
from ceg.runtime.fallback import (
    FallbackConfig,
    FallbackOrchestrator,
    FallbackPolicy,
)

# ── Custom exceptions ─────────────────────────────────────────────────────────


class NoEligibleModelError(Exception):
    """Raised when no model satisfies all hard constraints for a given node."""

    def __init__(self, node_id: str, reason: str = "") -> None:
        self.node_id = node_id
        msg = f"No eligible model found for node '{node_id}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


# ── Model registry ────────────────────────────────────────────────────────────

# Capability quality ratings per model (capability -> quality score [0,1]).
# In production this would come from benchmarks; here it's static mock data.
_CAPABILITY_RATINGS: dict[str, dict[str, float]] = {
    "fast-mini": {
        "data_retrieval": 0.80,
        "data_analysis": 0.60,
        "anomaly_detection": 0.50,
        "text_generation": 0.70,
        "summarization": 0.65,
        # S4 additions
        "data_reading": 0.78,
        "validation": 0.72,
        "aggregation": 0.65,
        "computation": 0.70,
        "trend_analysis": 0.55,
        "reasoning": 0.50,
        "notification": 0.75,
    },
    "fast-lite": {
        "data_retrieval": 0.85,
        "data_analysis": 0.65,
        "anomaly_detection": 0.55,
        "text_generation": 0.75,
        "summarization": 0.70,
        # S4 additions
        "data_reading": 0.82,
        "validation": 0.76,
        "aggregation": 0.68,
        "computation": 0.73,
        "trend_analysis": 0.58,
        "reasoning": 0.55,
        "notification": 0.78,
    },
    "balanced-standard": {
        "data_retrieval": 0.90,
        "data_analysis": 0.82,
        "anomaly_detection": 0.78,
        "text_generation": 0.85,
        "summarization": 0.88,
        # S4 additions
        "data_reading": 0.88,
        "validation": 0.85,
        "aggregation": 0.83,
        "computation": 0.84,
        "trend_analysis": 0.80,
        "reasoning": 0.79,
        "notification": 0.86,
    },
    "balanced-plus": {
        "data_retrieval": 0.92,
        "data_analysis": 0.85,
        "anomaly_detection": 0.80,
        "text_generation": 0.88,
        "summarization": 0.90,
        # S4 additions
        "data_reading": 0.90,
        "validation": 0.87,
        "aggregation": 0.86,
        "computation": 0.87,
        "trend_analysis": 0.83,
        "reasoning": 0.82,
        "notification": 0.88,
    },
    "quality-pro": {
        "data_retrieval": 0.95,
        "data_analysis": 0.93,
        "anomaly_detection": 0.92,
        "text_generation": 0.96,
        "summarization": 0.97,
        # S4 additions
        "data_reading": 0.96,
        "validation": 0.95,
        "aggregation": 0.94,
        "computation": 0.95,
        "trend_analysis": 0.93,
        "reasoning": 0.94,
        "notification": 0.96,
    },
}


# ── Pydantic models ───────────────────────────────────────────────────────────


class ModelProfile(BaseModel):
    """Description of a (mock) LLM model available in the registry.

    Attributes:
        name: Unique model identifier (e.g. "fast-mini").
        tier: Capability tier — fast / balanced / quality.
        estimated_cost: Estimated monetary cost per execution.
        estimated_latency_ms: Estimated latency in milliseconds.
        supported_capabilities: List of task capabilities this model can handle.
    """

    name: str = Field(..., min_length=1)
    tier: ModelTierHint
    estimated_cost: float = Field(..., ge=0.0)
    estimated_latency_ms: float = Field(..., ge=0.0)
    supported_capabilities: list[str] = Field(default_factory=list)

    def supports(self, capabilities: list[str]) -> bool:
        """Return True if this model supports ALL required capabilities."""
        return all(cap in self.supported_capabilities for cap in capabilities)

    def quality_rating_for(self, capabilities: list[str]) -> float:
        """Return the mean quality score across the requested capabilities.

        Uses the static ``_CAPABILITY_RATINGS`` registry.  Unknown capabilities
        receive a conservative default of 0.5.
        """
        if not capabilities:
            return 1.0
        ratings = _CAPABILITY_RATINGS.get(self.name, {})
        scores = [ratings.get(cap, 0.5) for cap in capabilities]
        return sum(scores) / len(scores)


class Constraint(BaseModel):
    """Hard execution constraints verified before model selection.

    Attributes:
        budget_remaining: Maximum allowed cost for the next execution.
        max_latency_ms: Maximum allowed latency in milliseconds.
    """

    budget_remaining: float = Field(..., ge=0.0)
    max_latency_ms: float = Field(..., ge=0.0)


# ── Selection weights ─────────────────────────────────────────────────────────


@dataclass
class SelectionWeights:
    """Scoring weights for the model selection algorithm.

    The three weights must conceptually sum to 1.0 (not enforced at runtime
    to allow experimentation). Defaults favour quality (w1=0.5).

    Attributes:
        w1: Weight for quality score (capability quality).
        w2: Weight for cost efficiency (inverse of cost).
        w3: Weight for speed (inverse of latency).
    """

    w1: float = 0.5  # quality
    w2: float = 0.3  # cost efficiency
    w3: float = 0.2  # speed

    def __post_init__(self) -> None:
        total = self.w1 + self.w2 + self.w3
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"SelectionWeights must sum to 1.0, got {total:.6f}.")


# ── Static model registry ─────────────────────────────────────────────────────

_ALL_CAPABILITIES = [
    "data_retrieval",
    "data_analysis",
    "anomaly_detection",
    "text_generation",
    "summarization",
    # S4 additions
    "data_reading",
    "validation",
    "aggregation",
    "computation",
    "trend_analysis",
    "reasoning",
    "notification",
]

DEFAULT_MODEL_REGISTRY: list[ModelProfile] = [
    ModelProfile(
        name="fast-mini",
        tier=ModelTierHint.FAST,
        estimated_cost=0.002,
        estimated_latency_ms=80.0,
        supported_capabilities=_ALL_CAPABILITIES,
    ),
    ModelProfile(
        name="fast-lite",
        tier=ModelTierHint.FAST,
        estimated_cost=0.004,
        estimated_latency_ms=100.0,
        supported_capabilities=_ALL_CAPABILITIES,
    ),
    ModelProfile(
        name="balanced-standard",
        tier=ModelTierHint.BALANCED,
        estimated_cost=0.012,
        estimated_latency_ms=200.0,
        supported_capabilities=_ALL_CAPABILITIES,
    ),
    ModelProfile(
        name="balanced-plus",
        tier=ModelTierHint.BALANCED,
        estimated_cost=0.020,
        estimated_latency_ms=280.0,
        supported_capabilities=_ALL_CAPABILITIES,
    ),
    ModelProfile(
        name="quality-pro",
        tier=ModelTierHint.QUALITY,
        estimated_cost=0.060,
        estimated_latency_ms=500.0,
        supported_capabilities=_ALL_CAPABILITIES,
    ),
]


# ── Core selection algorithm ──────────────────────────────────────────────────


def select_model(
    node: CEGNode,
    constraints: Constraint,
    available_models: list[ModelProfile],
    weights: SelectionWeights | None = None,
) -> ModelProfile:
    """Select the best model for a given node under the specified constraints.

    Algorithm (spec-compliant):
        Score = w1 * quality_capability + w2 * (1/cost) + w3 * (1/latency)

    Hard constraints (candidates that violate any are silently excluded):
        - ``estimated_cost``       ≤ ``constraints.budget_remaining``
        - ``estimated_latency_ms`` ≤ ``constraints.max_latency_ms``
        - model must support all ``node.required_capabilities``

    Args:
        node: The CEGNode requesting model selection.
        constraints: Hard resource constraints.
        available_models: Pool of candidate ModelProfile objects.
        weights: Scoring weights (defaults to ``SelectionWeights()``).

    Returns:
        The highest-scoring eligible ``ModelProfile``.

    Raises:
        NoEligibleModelError: If no model satisfies all hard constraints.
    """
    if weights is None:
        weights = SelectionWeights()

    candidates: list[tuple[ModelProfile, float]] = []

    for model in available_models:
        # Hard constraint 1: budget
        if model.estimated_cost > constraints.budget_remaining:
            continue
        # Hard constraint 2: latency
        if model.estimated_latency_ms > constraints.max_latency_ms:
            continue
        # Hard constraint 3: required capabilities
        if not model.supports(node.required_capabilities):
            continue

        quality_score = model.quality_rating_for(node.required_capabilities)
        cost_score = 1.0 / (model.estimated_cost + 0.001)
        latency_score = 1.0 / (model.estimated_latency_ms + 0.001)
        score = (
            weights.w1 * quality_score
            + weights.w2 * cost_score
            + weights.w3 * latency_score
        )
        candidates.append((model, score))

    if not candidates:
        caps = node.required_capabilities or ["<none>"]
        raise NoEligibleModelError(
            node_id=node.id,
            reason=(
                f"budget_remaining={constraints.budget_remaining:.4f}, "
                f"max_latency_ms={constraints.max_latency_ms:.1f}, "
                f"required_capabilities={caps}"
            ),
        )

    return max(candidates, key=lambda x: x[1])[0]


# ── RuntimeDecisionEngine ─────────────────────────────────────────────────────


class RuntimeDecisionEngine:
    """Stateful orchestrator injected into each LangGraph node at compile time.

    Responsibilities:
      - Maintains a global budget counter shared across all node executions.
      - Selects the optimal model before each node execution.
      - Delegates execution to the MockExecutor.
      - Triggers the FallbackOrchestrator on failure.
      - Exposes ``run_node()`` as the single entry point for node wrappers.

    Args:
        budget_total: Total cost budget for the entire graph execution.
        max_latency_ms: Maximum allowed latency per node execution (ms).
        available_models: Pool of ModelProfile objects; defaults to the
            built-in ``DEFAULT_MODEL_REGISTRY``.
        weights: Scoring weights for ``select_model()``; defaults to
            ``SelectionWeights()``.
        fallback_config: Default fallback configuration for all nodes.
    """

    def __init__(
        self,
        budget_total: float = 1.0,
        max_latency_ms: float = 1000.0,
        available_models: list[ModelProfile] | None = None,
        weights: SelectionWeights | None = None,
        fallback_config: FallbackConfig | None = None,
    ) -> None:
        self.budget_total = budget_total
        self.budget_used: float = 0.0
        self.max_latency_ms = max_latency_ms
        self.available_models: list[ModelProfile] = (
            available_models
            if available_models is not None
            else list(DEFAULT_MODEL_REGISTRY)
        )
        self.weights: SelectionWeights = weights or SelectionWeights()
        self.fallback_config: FallbackConfig = fallback_config or FallbackConfig(
            policy=FallbackPolicy.RETRY,
            max_retries=3,
            escalation_chain=[
                FallbackPolicy.RETRY,
                FallbackPolicy.ESCALATION,
                FallbackPolicy.ABORT,
            ],
        )
        self._orchestrator = FallbackOrchestrator(
            config=self.fallback_config,
            engine=self,
            weights=self.weights,
        )

    # ── Budget API ────────────────────────────────────────────────────────────

    @property
    def budget_remaining(self) -> float:
        """Remaining budget = total − used."""
        return max(0.0, self.budget_total - self.budget_used)

    @property
    def default_constraint(self) -> Constraint:
        """Current constraint snapshot reflecting the live budget."""
        return Constraint(
            budget_remaining=self.budget_remaining,
            max_latency_ms=self.max_latency_ms,
        )

    def spend(self, amount: float) -> None:
        """Record a cost expenditure against the global budget."""
        self.budget_used += amount

    # ── Main entry point ──────────────────────────────────────────────────────

    def run_node(
        self,
        node: CEGNode,
        state: dict[str, Any],
        executor: Any,  # MockExecutor — avoid circular import at type level
    ) -> dict[str, Any]:
        """Execute a CEGNode through the full decision pipeline.

        Steps:
          1. Check if budget allows any execution at all.
          2. Select the best model for the node.
          3. Attempt execution via the MockExecutor.
          4. On success: update budget and return state update.
          5. On failure: delegate to FallbackOrchestrator.

        Args:
            node: The CEGNode to execute.
            state: Current shared LangGraph state (CEGState snapshot).
            executor: A MockExecutor (or compatible) instance.

        Returns:
            A partial state update dict compatible with CEGState reducers.

        Raises:
            NodeAbortError: If the Abort fallback is triggered.
        """
        from ceg.compiler.mock_executor import ExecutionError

        # ── Step 1: pre-flight budget check ──────────────────────────────
        constraints = self.default_constraint
        if self.budget_remaining <= 0.0:
            # No budget at all → Degradation first, then Abort
            return self._handle_budget_exhausted(node, executor, state)

        # ── Step 2: model selection ───────────────────────────────────────
        try:
            model = select_model(
                node=node,
                constraints=constraints,
                available_models=self.available_models,
                weights=self.weights,
            )
        except NoEligibleModelError:
            # Budget insufficient for any model → degradation path
            return self._handle_budget_exhausted(node, executor, state)

        # ── Step 3: attempt execution ─────────────────────────────────────
        try:
            result = executor.execute(
                node_id=node.id,
                objective=node.objective,
                inputs=state.get("node_outputs", {}),
            )
            self.spend(result.cost)
            return {
                "node_outputs": {node.id: result.output},
                "node_statuses": {node.id: "completed"},
                "total_cost": result.cost,
                "total_latency_ms": result.latency_ms,
                "execution_log": [
                    {
                        "node_id": node.id,
                        "status": "completed",
                        "output": result.output,
                        "cost": result.cost,
                        "latency_ms": result.latency_ms,
                        "confidence": result.confidence,
                        "model_used": model.name,
                        "attempt": 1,
                    }
                ],
            }

        except ExecutionError as exc:
            # ── Step 4: trigger fallback chain ────────────────────────────
            return self._orchestrator.run(
                node=node,
                executor=executor,
                state=state,
                model=model,
                error=exc,
            )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _handle_budget_exhausted(
        self,
        node: CEGNode,
        executor: Any,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle the case where no model can be funded by the current budget.

        Strategy: try Degradation (cheaper truncated execution), then Abort.
        """
        from ceg.runtime.fallback import apply_abort, apply_degradation

        # Find cheapest available model (ignoring budget constraint)
        if self.available_models:
            cheapest = min(self.available_models, key=lambda m: m.estimated_cost)
            # Try degradation with very loose latency
            result = apply_degradation(
                node=node,
                executor=executor,
                engine=self,
                state=state,
                model=cheapest,
            )
            if result is not None:
                return result

        apply_abort(
            node, reason="Budget exhausted — no eligible model after degradation."
        )
        # unreachable, but satisfies type checker
        return {}  # pragma: no cover
