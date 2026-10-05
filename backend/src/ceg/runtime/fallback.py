"""Fallback strategies for the CEG Runtime Decision Engine.

Defines five distinct recovery policies that can be applied when a node
execution fails.  Each strategy is a pure function operating on the shared
CEGState snapshot and returning either a state update dict (success path) or
raising a dedicated exception (abort / propagation path).

Strategies (in escalation order of severity):
  1. Retry      — retry the same model up to N times
  2. Escalation — promote to a higher model tier
  3. Degradation — simplify the node objective and relax constraints
  4. Skip       — mark node as skipped and continue the graph
  5. Abort      — halt the entire graph execution immediately
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ceg.compiler.mock_executor import Executor
    from ceg.models.node import CEGNode
    from ceg.runtime.decision_engine import (
        ModelProfile,
        RuntimeDecisionEngine,
        SelectionWeights,
    )

logger = logging.getLogger(__name__)


# ── Custom exceptions ─────────────────────────────────────────────────────────


class NodeAbortError(Exception):
    """Raised when the Abort fallback strategy is triggered.

    Propagated up to the LangGraph node wrapper, causing the entire
    graph execution to halt immediately.

    Attributes:
        fallbacks_triggered: Strategies tried before aborting, in order.
    """

    def __init__(
        self,
        node_id: str,
        reason: str,
        fallbacks_triggered: list[str] | None = None,
    ) -> None:
        self.node_id = node_id
        self.reason = reason
        self.fallbacks_triggered: list[str] = list(fallbacks_triggered or [])
        super().__init__(f"Abort on node '{node_id}': {reason}")


class NodeSkippedError(Exception):
    """Raised internally when a node is deliberately skipped.

    Caught by the LangGraph node wrapper, which converts it into a
    ``status=skipped`` state update so the graph can continue.
    """

    def __init__(self, node_id: str, reason: str) -> None:
        self.node_id = node_id
        self.reason = reason
        super().__init__(f"Node '{node_id}' was skipped: {reason}")


class FallbackExhaustedError(NodeAbortError):
    """Raised when the entire fallback chain is exhausted without success."""

    def __init__(
        self, node_id: str, fallbacks_triggered: list[str] | None = None
    ) -> None:
        self.node_id = node_id
        self.reason = "fallback chain exhausted"
        self.fallbacks_triggered = list(fallbacks_triggered or [])
        Exception.__init__(
            self,
            f"All fallback strategies exhausted for node '{node_id}'. "
            "Aborting execution.",
        )


# ── Policy enum ───────────────────────────────────────────────────────────────


class FallbackPolicy(str, Enum):
    """Available fallback strategies, ordered from least to most disruptive."""

    RETRY = "retry"
    ESCALATION = "escalation"
    DEGRADATION = "degradation"
    SKIP = "skip"
    ABORT = "abort"


# ── Configuration ─────────────────────────────────────────────────────────────


@dataclass
class FallbackConfig:
    """Per-node (or graph-wide) fallback configuration.

    Args:
        policy: Primary fallback strategy to apply on first failure.
        max_retries: Maximum number of retry attempts (used by RETRY).
        escalation_chain: Ordered sequence of strategies to try in succession
            if the primary ``policy`` fails to recover the node.
            Defaults to ``[RETRY, ESCALATION, ABORT]``.
    """

    policy: FallbackPolicy = FallbackPolicy.RETRY
    max_retries: int = 3
    escalation_chain: list[FallbackPolicy] = field(
        default_factory=lambda: [
            FallbackPolicy.RETRY,
            FallbackPolicy.ESCALATION,
            FallbackPolicy.ABORT,
        ]
    )


# ── Strategy implementations ──────────────────────────────────────────────────


def apply_retry(
    node: CEGNode,
    executor: Executor,
    engine: RuntimeDecisionEngine,
    state: dict[str, Any],
    model: ModelProfile,
    max_retries: int,
    attempt: int,
) -> dict[str, Any] | None:
    """Retry the same model up to ``max_retries`` times.

    Returns a state-update dict on success, or ``None`` when retries are
    exhausted (caller should proceed to the next strategy in the chain).
    """
    from ceg.compiler.mock_executor import ExecutionError  # avoid circular import
    from ceg.runtime.decision_engine import executor_inputs

    for try_num in range(1, max_retries + 1):
        try:
            result = executor.execute(
                node_id=node.id,
                objective=node.objective,
                inputs=executor_inputs(state),
                attempt=attempt + try_num,
                model=model,
            )
        except ExecutionError as exc:
            logger.warning(
                "Retry %d/%d failed for node '%s': %s",
                try_num,
                max_retries,
                node.id,
                exc,
            )
            continue
        engine.spend(result.cost)
        return _make_success_update(node.id, result, model.name, try_num + 1)

    return None


def apply_escalation(
    node: CEGNode,
    executor: Executor,
    engine: RuntimeDecisionEngine,
    state: dict[str, Any],
    current_model: ModelProfile,
    weights: SelectionWeights,
) -> dict[str, Any] | None:
    """Promote to the next model tier and re-attempt execution.

    Tier order: fast → balanced → quality.
    Returns a state-update dict on success, or ``None`` if no higher tier
    model is eligible.
    """
    from ceg.compiler.mock_executor import ExecutionError
    from ceg.models.node import ModelTierHint
    from ceg.runtime.decision_engine import executor_inputs, rank_models

    tier_order = [
        ModelTierHint.FAST,
        ModelTierHint.BALANCED,
        ModelTierHint.QUALITY,
    ]
    try:
        current_tier_idx = tier_order.index(current_model.tier)
    except ValueError:
        return None

    higher_models = [
        m
        for m in engine.available_models
        if m.tier in tier_order[current_tier_idx + 1 :]
    ]
    # The tier hint is what failed: escalation compares every higher tier.
    unhinted = node.model_copy(update={"model_tier_hint": None})
    ranked = rank_models(unhinted, engine.constraint_for(state), higher_models, weights)
    if not ranked:
        return None

    best = ranked[0][0]
    try:
        result = executor.execute(
            node_id=node.id,
            objective=node.objective,
            inputs=executor_inputs(state),
            model=best,
        )
    except ExecutionError as exc:
        logger.warning(
            "Escalation to '%s' failed for node '%s': %s", best.name, node.id, exc
        )
        return None
    engine.spend(result.cost)
    return _make_success_update(node.id, result, best.name)


def apply_degradation(
    node: CEGNode,
    executor: Executor,
    engine: RuntimeDecisionEngine,
    state: dict[str, Any],
    model: ModelProfile,
    truncate_ratio: float = 0.5,
) -> dict[str, Any] | None:
    """Run a simplified, cheaper version of the node.

    Simulation of a reduced task: the objective is truncated to
    ``truncate_ratio`` of its length and the model is billed
    ``truncate_ratio`` of its estimated cost and latency. A real deployment
    would instead shorten the prompt or relax the requested output.

    The degraded run must fit in the remaining budget and latency budget:
    degradation never spends what the engine does not have.

    Returns a state-update dict on success, or ``None`` on failure (or when
    the degraded run does not fit in the budget).
    """
    from ceg.compiler.mock_executor import ExecutionError
    from ceg.runtime.decision_engine import executor_inputs

    degraded_model = model.model_copy(
        update={
            "estimated_cost": model.estimated_cost * truncate_ratio,
            "estimated_latency_ms": model.estimated_latency_ms * truncate_ratio,
        }
    )
    limits = engine.constraint_for(state)
    if (
        degraded_model.estimated_cost > limits.budget_remaining
        or degraded_model.estimated_latency_ms > limits.max_latency_ms
    ):
        return None

    truncated_objective = node.objective[
        : max(1, int(len(node.objective) * truncate_ratio))
    ]
    try:
        result = executor.execute(
            node_id=node.id,
            objective=truncated_objective,
            inputs=executor_inputs(state),
            model=degraded_model,
        )
    except ExecutionError as exc:
        logger.warning("Degradation failed for node '%s': %s", node.id, exc)
        return None
    engine.spend(result.cost)
    output = dict(result.output) if isinstance(result.output, dict) else result.output
    if isinstance(output, dict):
        output["degraded"] = True
        output["original_objective"] = node.objective
    return _make_success_update(node.id, result, model.name, extra_output=output)


def apply_skip(node: CEGNode) -> dict[str, Any]:
    """Mark the node as skipped and return a neutral state update.

    The graph execution continues; downstream nodes that depend on this
    node will receive ``None`` as its output.
    """
    return {
        "node_outputs": {node.id: None},
        "node_statuses": {node.id: "skipped"},
        "total_cost": 0.0,
        "total_latency_ms": 0.0,
        "execution_log": [
            {
                "node_id": node.id,
                "status": "skipped",
                "output": None,
                "cost": 0.0,
                "latency_ms": 0.0,
                "confidence": 0.0,
                "model_used": None,
            }
        ],
    }


def apply_abort(
    node: CEGNode,
    reason: str,
    fallbacks_triggered: list[str] | None = None,
) -> None:
    """Halt graph execution immediately by raising ``NodeAbortError``."""
    raise NodeAbortError(
        node_id=node.id, reason=reason, fallbacks_triggered=fallbacks_triggered
    )


# ── Orchestrator ──────────────────────────────────────────────────────────────


class FallbackOrchestrator:
    """Orchestrates the fallback chain for a failing node.

    Iterates through ``config.escalation_chain`` in order, applying each
    strategy until one succeeds or the chain is exhausted (which triggers
    an implicit Abort).

    Args:
        config: Fallback configuration (policy, max_retries, escalation_chain).
        engine: The RuntimeDecisionEngine instance for budget tracking.
        weights: Selection weights used by Escalation.
    """

    def __init__(
        self,
        config: FallbackConfig,
        engine: RuntimeDecisionEngine,
        weights: SelectionWeights,
    ) -> None:
        self.config = config
        self.engine = engine
        self.weights = weights

    def run(
        self,
        node: CEGNode,
        executor: Executor,
        state: dict[str, Any],
        model: ModelProfile,
        error: Exception,
    ) -> dict[str, Any]:
        """Apply the escalation chain and return the first successful state update.

        Args:
            node: The CEGNode that failed.
            executor: The executor running the node.
            state: Current LangGraph shared state.
            model: The model that was selected when the failure occurred.
            error: The original exception.

        Returns:
            A partial state update dict (compatible with CEGState reducers).

        Raises:
            NodeAbortError: If ABORT is reached or the chain is fully exhausted.
        """
        logger.warning("Node '%s' failed, starting fallback chain: %s", node.id, error)
        tried: list[str] = []
        for policy in self.config.escalation_chain:
            tried.append(policy.value)
            result: dict[str, Any] | None = None

            if policy == FallbackPolicy.RETRY:
                result = apply_retry(
                    node=node,
                    executor=executor,
                    engine=self.engine,
                    state=state,
                    model=model,
                    max_retries=self.config.max_retries,
                    attempt=1,
                )
            elif policy == FallbackPolicy.ESCALATION:
                result = apply_escalation(
                    node=node,
                    executor=executor,
                    engine=self.engine,
                    state=state,
                    current_model=model,
                    weights=self.weights,
                )
            elif policy == FallbackPolicy.DEGRADATION:
                result = apply_degradation(
                    node=node,
                    executor=executor,
                    engine=self.engine,
                    state=state,
                    model=model,
                )
            elif policy == FallbackPolicy.SKIP:
                result = apply_skip(node)
            elif policy == FallbackPolicy.ABORT:
                apply_abort(node, reason=str(error), fallbacks_triggered=tried)

            if result is not None:
                for entry in result["execution_log"]:
                    entry["fallbacks_triggered"] = list(tried)
                    entry["error"] = str(error)
                return result

        # Chain fully exhausted without success → implicit abort
        raise FallbackExhaustedError(node.id, fallbacks_triggered=tried)


# ── Private helpers ───────────────────────────────────────────────────────────


def _make_success_update(
    node_id: str,
    result: Any,
    model_name: str,
    attempt: int = 1,
    extra_output: Any = None,
) -> dict[str, Any]:
    """Build a CEGState-compatible state update dict from an ExecutionResult."""
    output = extra_output if extra_output is not None else result.output
    return {
        "node_outputs": {node_id: output},
        "node_statuses": {node_id: "completed"},
        "total_cost": result.cost,
        "total_latency_ms": result.latency_ms,
        "execution_log": [
            {
                "node_id": node_id,
                "status": "completed",
                "output": output,
                "cost": result.cost,
                "latency_ms": result.latency_ms,
                "confidence": result.confidence,
                "model_used": model_name,
                "attempt": attempt,
            }
        ],
    }


# Public type alias for node-function callables
NodeFn = Callable[[dict[str, Any]], dict[str, Any]]
