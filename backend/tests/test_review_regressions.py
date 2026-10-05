"""Regression tests for the defects found in the code review of October 2026.

Each test pins one behaviour that used to be wrong:
  - model selection ignored the tier hint and always picked the cheapest model;
  - the selected model never reached the executor (cost did not depend on it);
  - graph inputs given to ``invoke()`` were dropped;
  - a subgraph saw neither the parent inputs nor the upstream outputs;
  - a subgraph with a conditionally skipped node was reported as failed;
  - the budget leaked from one ``invoke()`` to the next and could be overspent.
"""

from __future__ import annotations

from typing import Any

import pytest

from ceg.compiler.compiler import CEGCompiler
from ceg.compiler.mock_executor import ExecutionResult, MockExecutor
from ceg.models.graph import CEGEdge, CEGGraph, EdgeType
from ceg.models.node import CEGNode, ModelTierHint
from ceg.runtime.decision_engine import (
    DEFAULT_MODEL_REGISTRY,
    Constraint,
    RuntimeDecisionEngine,
    SelectionWeights,
    select_model,
)
from ceg.runtime.fallback import FallbackConfig, FallbackPolicy, NodeAbortError

PRICES = {m.name: m.estimated_cost for m in DEFAULT_MODEL_REGISTRY}
WIDE_CONSTRAINT = Constraint(budget_remaining=1.0, max_latency_ms=10_000.0)


class _EchoInputsExecutor(MockExecutor):
    """Returns the inputs it received, to observe what reaches a node."""

    def run(
        self,
        node_id: str,
        objective: str,
        inputs: dict[str, Any],
        attempt: int = 1,
    ) -> ExecutionResult:
        return ExecutionResult(
            output={"seen": dict(inputs)}, cost=0.0, latency_ms=1.0, confidence=1.0
        )


class TestModelSelection:
    @pytest.mark.parametrize("tier", list(ModelTierHint))
    def test_tier_hint_is_honoured(self, tier: ModelTierHint) -> None:
        node = CEGNode(id="n", objective="o", model_tier_hint=tier)
        chosen = select_model(node, WIDE_CONSTRAINT, DEFAULT_MODEL_REGISTRY)
        assert chosen.tier == tier

    def test_tier_hint_falls_back_when_tier_unaffordable(self) -> None:
        node = CEGNode(id="n", objective="o", model_tier_hint=ModelTierHint.QUALITY)
        cheap_only = Constraint(budget_remaining=0.01, max_latency_ms=10_000.0)
        chosen = select_model(node, cheap_only, DEFAULT_MODEL_REGISTRY)
        assert chosen.estimated_cost <= 0.01

    def test_invalid_tier_hint_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            CEGNode.model_validate(
                {"id": "n", "objective": "o", "model_tier_hint": "ultra"}
            )

    def test_tier_hint_accepts_plain_strings(self) -> None:
        node = CEGNode.model_validate(
            {"id": "n", "objective": "o", "model_tier_hint": "quality"}
        )
        assert node.model_tier_hint == ModelTierHint.QUALITY

    def test_quality_weight_can_beat_cost(self) -> None:
        """Normalised scores: a quality-heavy weighting changes the choice.

        Before normalisation ``1/cost`` reached ~333 for the cheapest model
        and no realistic weighting could select anything else.
        """
        node = CEGNode(
            id="n",
            objective="o",
            required_capabilities=["anomaly_detection", "reasoning"],
        )
        quality_first = SelectionWeights(w1=0.8, w2=0.1, w3=0.1)
        chosen = select_model(
            node, WIDE_CONSTRAINT, DEFAULT_MODEL_REGISTRY, quality_first
        )
        assert chosen.tier != ModelTierHint.FAST


class TestModelReachesExecutor:
    def test_cost_is_the_selected_model_price(self) -> None:
        graph = CEGGraph(
            nodes=[
                CEGNode(id="n", objective="o", model_tier_hint=ModelTierHint.QUALITY)
            ]
        )
        state = CEGCompiler().compile(graph).invoke()
        entry = state["execution_log"][0]
        assert entry["model_used"] == "quality-pro"
        assert state["total_cost"] == pytest.approx(PRICES["quality-pro"])

    def test_decision_is_logged(self) -> None:
        graph = CEGGraph(
            nodes=[
                CEGNode(id="n", objective="o", model_tier_hint=ModelTierHint.BALANCED)
            ]
        )
        entry = CEGCompiler().compile(graph).invoke()["execution_log"][0]
        decision = entry["decision"]
        assert decision["selected_model"] == entry["model_used"]
        assert decision["tier_hint"] == "balanced"
        assert entry["fallbacks_triggered"] == []

    def test_escalation_bills_the_higher_tier_model(self) -> None:
        engine = RuntimeDecisionEngine(
            fallback_config=FallbackConfig(
                max_retries=0,
                escalation_chain=[FallbackPolicy.ESCALATION, FallbackPolicy.ABORT],
            )
        )
        executor = MockExecutor(failure_nodes={"n"}, max_failures={"n": 1})
        graph = CEGGraph(
            nodes=[CEGNode(id="n", objective="o", model_tier_hint=ModelTierHint.FAST)]
        )
        state = CEGCompiler(engine=engine, executor=executor).compile(graph).invoke()
        entry = state["execution_log"][0]
        assert entry["model_used"] not in {"fast-mini", "fast-lite"}
        assert state["total_cost"] == pytest.approx(PRICES[entry["model_used"]])
        assert entry["fallbacks_triggered"] == ["escalation"]


class TestGraphInputs:
    def test_invoke_inputs_reach_the_executor(self) -> None:
        graph = CEGGraph(nodes=[CEGNode(id="a", objective="o")])
        workflow = CEGCompiler(executor=_EchoInputsExecutor()).compile(graph)
        state = workflow.invoke({"inputs": {"csv_path": "data.csv"}})
        assert state["node_outputs"]["a"]["seen"] == {"csv_path": "data.csv"}

    def test_subgraph_sees_parent_inputs_and_upstream_outputs(self) -> None:
        inner = CEGGraph(nodes=[CEGNode(id="inner", objective="o")])
        outer = CEGGraph(
            nodes=[
                CEGNode(id="first", objective="o"),
                CEGNode(id="team", objective="o", subgraph=inner),
            ],
            edges=[CEGEdge(source="first", target="team")],
        )
        workflow = CEGCompiler(executor=_EchoInputsExecutor()).compile(outer)
        state = workflow.invoke({"inputs": {"region": "Nord"}})
        seen = state["node_outputs"]["team"]["inner"]["seen"]
        assert seen["region"] == "Nord"
        assert "first" in seen


class TestSubgraphStatus:
    def test_conditional_skip_inside_subgraph_is_not_a_failure(self) -> None:
        inner = CEGGraph(
            nodes=[CEGNode(id="i1", objective="o"), CEGNode(id="i2", objective="o")],
            edges=[
                CEGEdge(
                    source="i1",
                    target="i2",
                    edge_type=EdgeType.CONDITIONAL,
                    condition="never_set",
                )
            ],
        )
        outer = CEGGraph(nodes=[CEGNode(id="team", objective="o", subgraph=inner)])
        state = CEGCompiler().compile(outer).invoke()
        assert state["node_statuses"]["team"] == "completed"


class TestBudget:
    def test_budget_is_reset_between_invocations(self) -> None:
        engine = RuntimeDecisionEngine(budget_total=0.01)
        graph = CEGGraph(
            nodes=[CEGNode(id="a", objective="o", model_tier_hint=ModelTierHint.FAST)]
        )
        workflow = CEGCompiler(engine=engine).compile(graph)
        models = [workflow.invoke()["execution_log"][0]["model_used"] for _ in range(4)]
        # Same budget each time → same decision each time, never degraded.
        assert len(set(models)) == 1
        assert engine.budget_used == pytest.approx(PRICES[models[0]])

    def test_budget_is_never_exceeded(self) -> None:
        engine = RuntimeDecisionEngine(budget_total=0.005)
        graph = CEGGraph(
            nodes=[CEGNode(id=f"n{i}", objective="o") for i in range(10)],
            edges=[CEGEdge(source=f"n{i}", target=f"n{i + 1}") for i in range(9)],
        )
        workflow = CEGCompiler(engine=engine).compile(graph)
        # Ten nodes do not fit in the budget: the run aborts instead of
        # overspending, after a last degraded node that still fits.
        with pytest.raises(NodeAbortError):
            workflow.invoke()
        assert engine.budget_used <= engine.budget_total + 1e-12
