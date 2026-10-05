"""Backend-independent pieces shared by every execution backend.

A backend only decides *how* the graph is driven (LangGraph super-steps,
a plain Python loop, ...). What a node does, how the declared constraints are
enforced and how the state is merged is defined once, here, so that every
backend produces the same results for the same task.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from ceg.compiler.mock_executor import Executor
from ceg.compiler.state import CEGState
from ceg.models.graph import CEGEdge, CEGGraph
from ceg.models.node import CEGNode
from ceg.runtime.decision_engine import RuntimeDecisionEngine

# Runs a sub-graph with the given graph inputs and returns its final state.
SubgraphRunner = Callable[[CEGGraph, dict[str, Any]], dict[str, Any]]


@dataclass
class StateSnapshot:
    """Saved state of an execution thread (same shape as LangGraph's).

    Attributes:
        values: The CEGState values at the last checkpoint.
        next: Nodes still to run (empty when the run is over).
    """

    values: dict[str, Any] = field(default_factory=dict)
    next: tuple[str, ...] = ()


# ── Declared constraints ──────────────────────────────────────────────────────


def hitl_node_ids(graph: CEGGraph) -> list[str]:
    """IDs of the nodes requiring a human decision, sub-graphs included."""
    found: list[str] = []
    for node in graph.nodes:
        if node.interrupt_before or node.interrupt_after:
            found.append(node.id)
        if node.subgraph is not None:
            found.extend(hitl_node_ids(node.subgraph))
    return found


def prepare_engine(
    graph: CEGGraph, engine: RuntimeDecisionEngine | None = None
) -> RuntimeDecisionEngine:
    """Return the engine for ``graph``, bound by the task's constraints.

    The task's ``max_cost_usd`` and ``max_latency_seconds`` cap the engine's
    budget and latency budget: a caller-supplied engine may be stricter than
    the declaration, never looser.
    """
    engine = engine or RuntimeDecisionEngine()
    constraints = graph.task.task_constraints if graph.task else None
    if constraints is not None:
        engine.budget_total = min(engine.budget_total, constraints.max_cost_usd)
        limit_ms = constraints.max_latency_seconds * 1000.0
        engine.max_total_latency_ms = (
            limit_ms
            if engine.max_total_latency_ms is None
            else min(engine.max_total_latency_ms, limit_ms)
        )
    return engine


def check_graph(graph: CEGGraph, engine: RuntimeDecisionEngine) -> None:
    """Refuse, before running anything, a graph that cannot honour its task.

    - every node's required capabilities are offered by an available model;
    - every tool a node uses is in the task's ``tools_allowed``.

    Raises:
        ValueError: Listing every offending node.
    """
    nodes = list(_all_nodes(graph))

    missing = {
        n.id: n.required_capabilities
        for n in nodes
        if not any(m.supports(n.required_capabilities) for m in engine.available_models)
    }
    if missing:
        raise ValueError(
            f"No available model offers the capabilities required by {missing}."
        )

    if graph.task is not None:
        allowed = set(graph.task.tools_allowed)
        forbidden = {
            n.id: sorted(set(n.tools) - allowed)
            for n in nodes
            if set(n.tools) - allowed
        }
        if forbidden:
            raise ValueError(
                f"Nodes use tools outside tools_allowed {sorted(allowed)}: {forbidden}."
            )


def _all_nodes(graph: CEGGraph) -> list[CEGNode]:
    nodes: list[CEGNode] = []
    for node in graph.nodes:
        nodes.append(node)
        if node.subgraph is not None:
            nodes.extend(_all_nodes(node.subgraph))
    return nodes


# ── Node execution ────────────────────────────────────────────────────────────


def loop_id(edge: CEGEdge) -> str:
    """Counter key of a LOOP edge in ``CEGState.loop_counts``."""
    return f"loop_{edge.source}_{edge.target}"


def execute_node(
    node: CEGNode,
    state: dict[str, Any],
    engine: RuntimeDecisionEngine,
    executor: Executor,
    *,
    loop_ids: list[str],
    run_subgraph: SubgraphRunner,
) -> dict[str, Any]:
    """Run one node (model selection, execution, fallbacks) → state update.

    A composite node runs its sub-graph through ``run_subgraph`` with the
    parent's graph inputs and upstream outputs as inputs. If the node is the
    source of LOOP edges, their iteration counters are incremented.
    """
    if node.subgraph is not None:
        sub_inputs = {**state.get("inputs", {}), **state.get("node_outputs", {})}
        result = subgraph_update(node, run_subgraph(node.subgraph, sub_inputs))
    else:
        result = engine.run_node(node=node, state=state, executor=executor)

    if loop_ids:
        counts = state.get("loop_counts", {})
        result["loop_counts"] = {lid: counts.get(lid, 0) + 1 for lid in loop_ids}
    return result


def subgraph_update(node: CEGNode, sub_result: dict[str, Any]) -> dict[str, Any]:
    """State update of a composite node from its sub-graph's final state."""
    sub_outputs = sub_result.get("node_outputs", {})
    sub_statuses: dict[str, str] = sub_result.get("node_statuses", {})
    sub_cost = float(sub_result.get("total_cost", 0.0))
    sub_latency = float(sub_result.get("total_latency_ms", 0.0))

    log: list[dict[str, Any]] = [
        {**entry, "subgraph_parent": node.id}
        for entry in sub_result.get("execution_log", [])
    ]
    # A branch skipped by a condition is a normal outcome, not a failure.
    status = (
        "completed"
        if all(st in ("completed", "skipped") for st in sub_statuses.values())
        else "failed"
    )
    log.append(
        {
            "node_id": node.id,
            "status": status,
            "output": sub_outputs,
            "cost": sub_cost,
            "latency_ms": sub_latency,
            "confidence": 1.0,
            "model_used": "subgraph_composite",
        }
    )
    return {
        "node_outputs": {node.id: sub_outputs},
        "node_statuses": {node.id: status},
        "total_cost": sub_cost,
        "total_latency_ms": sub_latency,
        "execution_log": log,
    }


def skip_update(node_id: str, reason: str | None = None) -> dict[str, Any]:
    """State update marking ``node_id`` as skipped."""
    entry: dict[str, Any] = {
        "node_id": node_id,
        "status": "skipped",
        "output": None,
        "cost": 0.0,
        "latency_ms": 0.0,
        "confidence": 0.0,
        "model_used": None,
    }
    if reason:
        entry["reason"] = reason
    return {
        "node_outputs": {node_id: None},
        "node_statuses": {node_id: "skipped"},
        "total_cost": 0.0,
        "total_latency_ms": 0.0,
        "execution_log": [entry],
    }


# ── State ─────────────────────────────────────────────────────────────────────


def initial_state(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Empty CEGState, updated with ``overrides``."""
    state: dict[str, Any] = {
        "inputs": {},
        "node_outputs": {},
        "node_statuses": {},
        "total_cost": 0.0,
        "total_latency_ms": 0.0,
        "execution_log": [],
        "loop_counts": {},
        "human_approvals": {},
    }
    if overrides:
        state.update(overrides)
    return state


def _reducers() -> dict[str, Callable[[Any, Any], Any]]:
    """The reducer of each CEGState key, read from its annotations."""
    hints = get_type_hints(CEGState, include_extras=True)
    return {
        key: hint.__metadata__[0]
        for key, hint in hints.items()
        if getattr(hint, "__metadata__", None)
    }


REDUCERS = _reducers()


def apply_update(state: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Merge a node's state update with CEGState's reducers (as LangGraph does)."""
    merged = dict(state)
    for key, value in update.items():
        reducer = REDUCERS.get(key)
        merged[key] = (
            reducer(merged[key], value) if reducer and key in merged else value
        )
    return merged
