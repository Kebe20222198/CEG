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

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ceg.models.node import CEGNode, ModelTierHint
from ceg.runtime.fallback import (
    FallbackConfig,
    FallbackOrchestrator,
    FallbackPolicy,
)
from ceg.runtime.statistics import ModelStatistics

if TYPE_CHECKING:
    from ceg.compiler.mock_executor import ExecutionError, ExecutionResult, Executor

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
        # Capabilities used by the loop and multi-agent demos
        "evaluation": 0.55,
        "web_search": 0.70,
        "information_retrieval": 0.75,
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
        # Capabilities used by the loop and multi-agent demos
        "evaluation": 0.60,
        "web_search": 0.74,
        "information_retrieval": 0.80,
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
        # Capabilities used by the loop and multi-agent demos
        "evaluation": 0.80,
        "web_search": 0.84,
        "information_retrieval": 0.87,
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
        # Capabilities used by the loop and multi-agent demos
        "evaluation": 0.83,
        "web_search": 0.86,
        "information_retrieval": 0.89,
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
        # Capabilities used by the loop and multi-agent demos
        "evaluation": 0.94,
        "web_search": 0.92,
        "information_retrieval": 0.94,
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

    The three weights must sum to 1.0 (checked in ``__post_init__``). Each
    weight applies to a score normalised to [0, 1], so the weights really set
    the trade-off between quality, cost and speed.

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
    "evaluation",
    "web_search",
    "information_retrieval",
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


def rank_models(
    node: CEGNode,
    constraints: Constraint,
    available_models: list[ModelProfile],
    weights: SelectionWeights | None = None,
    statistics: ModelStatistics | None = None,
) -> list[tuple[ModelProfile, float]]:
    """Return the eligible models for ``node`` with their score, best first.

    Algorithm (spec formula, with normalised terms):
        Score = w1 * Quality + w2 * norm(1/Cost) + w3 * norm(1/Latency)

    ``norm(1/x)`` divides ``1/x`` by its maximum over the compared models, so
    the cost and speed terms lie in [0, 1] like the quality term. Without this
    normalisation ``1/Cost`` reaches several hundred and the cheapest model
    always wins, whatever the weights.

    Hard constraints (candidates that violate any are excluded):
        - ``estimated_cost``       ≤ ``constraints.budget_remaining``
        - ``estimated_latency_ms`` ≤ ``constraints.max_latency_ms``
        - model must support all ``node.required_capabilities``

    Tier hint: when ``node.model_tier_hint`` is set and at least one eligible
    model belongs to that tier, only models of that tier are compared. If no
    model of the hinted tier is eligible, all eligible models are compared.

    Learned statistics: with ``statistics``, the quality term is the learned
    estimate (plus an exploration bonus) and the cost and latency terms use the
    expected cost and latency of a *successful* result, failed calls included
    (``ModelStatistics``). Without, the static registry values are used. The
    hard constraints always use the registry's per-call estimates.

    Returns:
        ``(model, score)`` pairs sorted by decreasing score (empty if none is
        eligible).
    """
    if weights is None:
        weights = SelectionWeights()

    eligible = [
        m
        for m in available_models
        if m.estimated_cost <= constraints.budget_remaining
        and m.estimated_latency_ms <= constraints.max_latency_ms
        and m.supports(node.required_capabilities)
    ]

    hint = node.model_tier_hint
    if hint is not None:
        in_tier = [m for m in eligible if m.tier == hint]
        if in_tier:
            eligible = in_tier

    if not eligible:
        return []

    caps = node.required_capabilities
    quality: dict[str, float] = {}
    cost: dict[str, float] = {}
    latency: dict[str, float] = {}
    for m in eligible:
        if statistics is None:
            quality[m.name] = m.quality_rating_for(caps)
            cost[m.name] = m.estimated_cost
            latency[m.name] = m.estimated_latency_ms
        else:
            quality[m.name] = statistics.ranking_quality(m, caps, eligible)
            cost[m.name] = statistics.expected_cost(m, caps)
            latency[m.name] = statistics.expected_latency_ms(m, caps)

    eps = 1e-6
    min_cost = min(cost.values())
    min_latency = min(latency.values())

    ranked: list[tuple[ModelProfile, float]] = []
    for model in eligible:
        quality_score = quality[model.name]
        cost_score = (min_cost + eps) / (cost[model.name] + eps)
        latency_score = (min_latency + eps) / (latency[model.name] + eps)
        score = (
            weights.w1 * quality_score
            + weights.w2 * cost_score
            + weights.w3 * latency_score
        )
        ranked.append((model, score))

    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked


def select_model(
    node: CEGNode,
    constraints: Constraint,
    available_models: list[ModelProfile],
    weights: SelectionWeights | None = None,
) -> ModelProfile:
    """Select the best model for a given node under the specified constraints.

    See ``rank_models()`` for the scoring algorithm and tier-hint handling.

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
    ranked = rank_models(node, constraints, available_models, weights)
    if not ranked:
        caps = node.required_capabilities or ["<none>"]
        raise NoEligibleModelError(
            node_id=node.id,
            reason=(
                f"budget_remaining={constraints.budget_remaining:.4f}, "
                f"max_latency_ms={constraints.max_latency_ms:.1f}, "
                f"required_capabilities={caps}"
            ),
        )
    return ranked[0][0]


# ── RuntimeDecisionEngine ─────────────────────────────────────────────────────


class RuntimeDecisionEngine:
    """Stateful orchestrator injected into each LangGraph node at compile time.

    Responsibilities:
      - Maintains a budget counter shared by all nodes of one graph execution
        (reset by ``CompiledWorkflow.invoke()``, kept across ``resume()``).
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
        max_total_latency_ms: Latency budget for the whole execution: the
            cumulated latency of the node executions never exceeds it. None
            means no limit. Set from ``TaskConstraint.max_latency_seconds``.
        statistics: Learned model statistics. When given, model selection
            uses them (learned optimiser) and every call — failed or not —
            is recorded into them, so the engine keeps learning. They can be
            shared by several engines and saved between sessions.
    """

    def __init__(
        self,
        budget_total: float = 1.0,
        max_latency_ms: float = 1000.0,
        available_models: list[ModelProfile] | None = None,
        weights: SelectionWeights | None = None,
        fallback_config: FallbackConfig | None = None,
        max_total_latency_ms: float | None = None,
        statistics: ModelStatistics | None = None,
    ) -> None:
        self.budget_total = budget_total
        self.budget_used: float = 0.0
        # Parallel branches run in worker threads and spend concurrently.
        self._budget_lock = threading.Lock()
        self.max_latency_ms = max_latency_ms
        self.max_total_latency_ms = max_total_latency_ms
        self.statistics = statistics
        # Failed calls of the node being run: billed, and added to its result.
        self._failed_calls: dict[str, list[tuple[float, float]]] = {}
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

    def constraint_for(self, state: dict[str, Any]) -> Constraint:
        """Constraints for the next node of the execution whose state is given.

        Adds the latency budget to ``default_constraint``: a node may not
        take longer than what remains of ``max_total_latency_ms`` once the
        latency already accumulated in ``state`` is deducted.
        """
        max_latency_ms = self.max_latency_ms
        if self.max_total_latency_ms is not None:
            spent = float(state.get("total_latency_ms", 0.0))
            max_latency_ms = min(
                max_latency_ms, max(0.0, self.max_total_latency_ms - spent)
            )
        return Constraint(
            budget_remaining=self.budget_remaining, max_latency_ms=max_latency_ms
        )

    def spend(self, amount: float) -> None:
        """Record a cost expenditure against the global budget."""
        with self._budget_lock:
            self.budget_used += amount

    def reset_budget(self) -> None:
        """Start a new graph execution with the full budget available."""
        with self._budget_lock:
            self.budget_used = 0.0

    # ── Call accounting ───────────────────────────────────────────────────────

    def record_success(
        self,
        node: CEGNode,
        model: ModelProfile,
        result: ExecutionResult,
        *,
        learn: bool = True,
    ) -> None:
        """Bill a successful call and learn from it.

        ``learn=False`` bills without learning (e.g. a degraded run, whose
        quality says nothing about the model on the full task).
        """
        self.spend(result.cost)
        if learn and self.statistics is not None:
            self.statistics.observe(
                model.name,
                node.required_capabilities,
                success=True,
                quality=result.confidence,
                cost=result.cost,
                latency_ms=result.latency_ms,
            )

    def record_failure(
        self,
        node: CEGNode,
        model: ModelProfile,
        error: ExecutionError,
        *,
        learn: bool = True,
    ) -> None:
        """Bill a failed call, keep it for the node's result, learn from it."""
        self.spend(error.cost)
        with self._budget_lock:
            self._failed_calls.setdefault(node.id, []).append(
                (error.cost, error.latency_ms)
            )
        if learn and self.statistics is not None:
            self.statistics.observe(
                model.name,
                node.required_capabilities,
                success=False,
                quality=0.0,
                cost=error.cost,
                latency_ms=error.latency_ms,
            )

    def _take_failed_calls(self, node_id: str) -> list[tuple[float, float]]:
        with self._budget_lock:
            return self._failed_calls.pop(node_id, [])

    # ── Main entry point ──────────────────────────────────────────────────────

    def run_node(
        self,
        node: CEGNode,
        state: dict[str, Any],
        executor: Executor,
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

        try:
            update = self._run_node(node, state, executor)
        finally:
            failed = self._take_failed_calls(node.id)
        return _with_failed_calls(update, failed)

    def _run_node(
        self,
        node: CEGNode,
        state: dict[str, Any],
        executor: Executor,
    ) -> dict[str, Any]:
        from ceg.compiler.mock_executor import ExecutionError

        # ── Step 1: constraints for this node (budget + latency left) ────
        constraints = self.constraint_for(state)

        # ── Step 2: model selection ───────────────────────────────────────
        ranked = rank_models(
            node=node,
            constraints=constraints,
            available_models=self.available_models,
            weights=self.weights,
            statistics=self.statistics,
        )
        if not ranked:
            return self._handle_no_eligible_model(node, executor, state)
        model, score = ranked[0]
        decision: dict[str, Any] = {
            "selected_model": model.name,
            "tier": model.tier.value,
            "tier_hint": node.model_tier_hint.value if node.model_tier_hint else None,
            "score": round(score, 6),
            "candidates": [m.name for m, _ in ranked],
            "budget_remaining": round(self.budget_remaining, 6),
            "quality_source": "static" if self.statistics is None else "learned",
        }
        if self.statistics is not None:
            caps = node.required_capabilities
            decision["expected_quality"] = round(
                self.statistics.quality_estimate(model, caps), 4
            )
            decision["observations"] = self.statistics.observations(model.name, caps)

        # ── Step 3: attempt execution ─────────────────────────────────────
        try:
            result = executor.execute(
                node_id=node.id,
                objective=node.objective,
                inputs=executor_inputs(state),
                model=model,
            )
        except ExecutionError as exc:
            self.record_failure(node, model, exc)
            # ── Step 4: trigger fallback chain ────────────────────────────
            update = self._orchestrator.run(
                node=node,
                executor=executor,
                state=state,
                model=model,
                error=exc,
            )
            for entry in update.get("execution_log", []):
                entry.setdefault("decision", decision)
            return update

        self.record_success(node, model, result)
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
                    "decision": decision,
                    "fallbacks_triggered": [],
                }
            ],
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _handle_no_eligible_model(
        self,
        node: CEGNode,
        executor: Executor,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """No model fits: refuse, or run a degraded version that still fits.

        - No model of the registry has the required capabilities: the node is
          aborted. Running it on a model lacking a capability would silently
          break the declaration.
        - Capable models exist but none fits the remaining budget or latency:
          Degradation on the cheapest capable model, then Abort.
        """
        from ceg.runtime.fallback import apply_abort, apply_degradation

        capable = [
            m for m in self.available_models if m.supports(node.required_capabilities)
        ]
        if not capable:
            apply_abort(
                node,
                reason=(
                    "No available model has the required capabilities "
                    f"{node.required_capabilities}."
                ),
            )

        cheapest = min(capable, key=lambda m: m.estimated_cost)
        result = apply_degradation(
            node=node,
            executor=executor,
            engine=self,
            state=state,
            model=cheapest,
        )
        if result is not None:
            result["execution_log"][0]["fallbacks_triggered"] = [
                FallbackPolicy.DEGRADATION.value
            ]
            return result

        apply_abort(
            node,
            reason=(
                "Budget or latency budget exhausted — no model fits, even degraded."
            ),
            fallbacks_triggered=[FallbackPolicy.DEGRADATION.value],
        )
        # unreachable, but satisfies type checker
        return {}  # pragma: no cover


def executor_inputs(state: dict[str, Any]) -> dict[str, Any]:
    """Inputs handed to an executor: graph inputs, then upstream node outputs.

    Node outputs take precedence over a graph input with the same key.
    """
    return {**state.get("inputs", {}), **state.get("node_outputs", {})}


def _with_failed_calls(
    update: dict[str, Any], failed: list[tuple[float, float]]
) -> dict[str, Any]:
    """Add the node's failed calls to its cost and latency.

    A failed LLM call is billed and takes time: the node's cost and latency
    are those of every call made for it, not only of the one that succeeded.
    """
    if not failed:
        return update
    failed_cost = sum(cost for cost, _ in failed)
    failed_latency = sum(latency for _, latency in failed)
    update["total_cost"] = update.get("total_cost", 0.0) + failed_cost
    update["total_latency_ms"] = update.get("total_latency_ms", 0.0) + failed_latency
    for entry in update.get("execution_log", [])[-1:]:
        entry["cost"] = entry.get("cost", 0.0) + failed_cost
        entry["latency_ms"] = entry.get("latency_ms", 0.0) + failed_latency
        entry["failed_calls"] = len(failed)
    return update
