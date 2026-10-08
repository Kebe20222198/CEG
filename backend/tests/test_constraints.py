"""Declared constraints are guaranteed at run time, or refused beforehand.

Each test runs on every backend: the guarantee belongs to CEG, not to the
engine that happens to execute the plan.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from ceg.backends import get_backend
from ceg.compiler.mock_executor import MockExecutor
from ceg.evaluation.engine import EvaluationEngine
from ceg.evaluation.judge import MockJudgeClient
from ceg.models.graph import CEGGraph
from ceg.models.node import CEGNode
from ceg.models.task import CognitiveTask, SubTask, TaskConstraint
from ceg.planner import plan
from ceg.runtime.decision_engine import BudgetUnavailableError, RuntimeDecisionEngine
from ceg.runtime.fallback import NodeAbortError
from ceg.use_cases.demo_pipelines import LoopReportExecutor, build_loop_report_graph
from ceg.use_cases.sales_criteria import ALL_SALES_CRITERIA

BACKENDS = ["langgraph", "python"]


def _chain(tier: str, n: int, **constraints: Any) -> CognitiveTask:
    """n sequential sub-tasks hinting ``tier``."""
    return CognitiveTask(
        objective="chaîne",
        task_constraints=TaskConstraint(**constraints),
        subtasks=[
            SubTask(
                id=f"s{i}",
                objective=f"étape {i}",
                model_tier_hint=tier,
                dependencies=[f"s{i - 1}"] if i else [],
            )
            for i in range(n)
        ],
    )


@pytest.mark.parametrize("backend", BACKENDS)
class TestRuntimeGuarantees:
    def test_declared_budget_is_never_exceeded(self, backend: str) -> None:
        """Three quality nodes (0.06 each) under a 0.10 USD budget."""
        graph = plan(_chain("quality", 3, max_cost_usd=0.10))
        state = get_backend(backend).compile(graph).invoke()
        assert state["total_cost"] <= 0.10 + 1e-9
        # The budget forced cheaper models after the first node.
        models = [e["model_used"] for e in state["execution_log"]]
        assert models[0] == "quality-pro"
        assert "quality-pro" not in models[1:]

    def test_declared_budget_caps_a_looser_engine(self, backend: str) -> None:
        engine = RuntimeDecisionEngine(budget_total=10.0)
        graph = plan(_chain("fast", 1, max_cost_usd=0.25))
        get_backend(backend).compile(graph, engine=engine)
        assert engine.budget_total == 0.25

    def test_declared_latency_is_never_exceeded(self, backend: str) -> None:
        """quality-pro takes 500 ms: the third node no longer fits in 1.2 s."""
        graph = plan(_chain("quality", 3, max_latency_seconds=1.2))
        state = get_backend(backend).compile(graph).invoke()
        assert state["total_latency_ms"] <= 1200.0
        models = [e["model_used"] for e in state["execution_log"]]
        assert models[:2] == ["quality-pro", "quality-pro"]
        assert models[2] != "quality-pro"

    def test_latency_budget_exhausted_aborts(self, backend: str) -> None:
        graph = plan(_chain("fast", 3, max_latency_seconds=0.1))
        with pytest.raises(NodeAbortError, match="latency"):
            get_backend(backend).compile(graph).invoke()


@pytest.mark.parametrize("backend", BACKENDS)
class TestRefusedBeforeRunning:
    def test_capability_no_model_offers(self, backend: str) -> None:
        graph = plan(
            CognitiveTask(
                objective="o",
                subtasks=[
                    SubTask(id="a", objective="a", required_capabilities=["telepathy"])
                ],
            )
        )
        with pytest.raises(ValueError, match="capabilities"):
            get_backend(backend).compile(graph)

    def test_tool_outside_tools_allowed_in_a_hand_built_graph(
        self, backend: str
    ) -> None:
        """The planner refuses it; the backend checks hand-built graphs too."""
        graph = CEGGraph(
            nodes=[CEGNode(id="a", objective="a", tools=["shell"])],
            task=CognitiveTask(objective="o", tools_allowed=["sql_query"]),
        )
        with pytest.raises(ValueError, match="tools_allowed"):
            get_backend(backend).compile(graph)


class TestNoSilentCapabilityDowngrade:
    def test_node_without_capable_model_aborts_instead_of_degrading(self) -> None:
        """It used to run "degraded" on the cheapest model, capable or not."""
        engine = RuntimeDecisionEngine()
        node = CEGNode(id="a", objective="a", required_capabilities=["telepathy"])
        with pytest.raises(NodeAbortError, match="capabilities"):
            engine.run_node(node, {"node_outputs": {}}, MockExecutor())

    @pytest.mark.parametrize("backend", BACKENDS)
    def test_loop_critique_runs_on_a_capable_model(self, backend: str) -> None:
        workflow = get_backend(backend).compile(
            build_loop_report_graph(), executor=LoopReportExecutor()
        )
        log = workflow.invoke()["execution_log"]
        critiques = [e for e in log if e["node_id"] == "evaluer_critique"]
        assert critiques
        for entry in critiques:
            assert entry["fallbacks_triggered"] == []
            assert not entry["output"].get("degraded", False)


class TestQualityIsVerifiedAfterwards:
    def _state(self) -> dict[str, Any]:
        return {
            "node_outputs": {"detect_anomaly": {"anomalies_found": True}},
            "node_statuses": {"detect_anomaly": "completed"},
            "total_cost": 0.01,
            "total_latency_ms": 100.0,
            "execution_log": [],
        }

    def test_quality_below_minimum_is_a_violation(self) -> None:
        engine = EvaluationEngine(
            judge=MockJudgeClient(default_score=0.80), criteria=ALL_SALES_CRITERIA
        )
        report = engine.evaluate(
            self._state(), constraints=TaskConstraint(min_quality_score=0.85)
        )
        assert not report.meets_constraints
        assert "min_quality_score" in report.constraint_violations[0]

    def test_quality_above_minimum_meets_constraints(self) -> None:
        engine = EvaluationEngine(
            judge=MockJudgeClient(default_score=0.90), criteria=ALL_SALES_CRITERIA
        )
        report = engine.evaluate(
            self._state(), constraints=TaskConstraint(min_quality_score=0.85)
        )
        assert report.meets_constraints
        assert report.constraints_unverified == []

    def test_unmeasured_quality_is_unverified_not_violated(self) -> None:
        report = EvaluationEngine().evaluate(
            self._state(), constraints=TaskConstraint(min_quality_score=0.85)
        )
        assert report.meets_constraints
        assert report.constraints_unverified == ["min_quality_score"]

    def test_constraints_set_the_composite_score_ceilings(self) -> None:
        report = EvaluationEngine().evaluate(
            self._state(),
            constraints=TaskConstraint(max_cost_usd=0.02, max_latency_seconds=1.0),
        )
        assert report.max_budget_usd == 0.02
        assert report.max_latency_seconds == 1.0


class _SlowExecutor(MockExecutor):
    """Calls that take real time, so parallel branches truly overlap."""

    def run(
        self, node_id: str, objective: str, inputs: dict[str, Any], attempt: int = 1
    ) -> Any:
        time.sleep(0.1)
        return super().run(node_id, objective, inputs, attempt)


def _fan_out(**constraints: Any) -> CognitiveTask:
    """``init`` then three branches running in parallel."""
    return CognitiveTask(
        objective="o",
        task_constraints=TaskConstraint(**constraints),
        subtasks=[SubTask(id="init", objective="i", model_tier_hint="fast")]
        + [
            SubTask(
                id=f"b{i}", objective="b", dependencies=["init"], model_tier_hint="fast"
            )
            for i in range(3)
        ],
    )


@pytest.mark.parametrize("backend", BACKENDS)
class TestParallelBranchesShareTheBudgets:
    """Regression: parallel branches each saw the whole remaining budget.

    With calls that take time, three LangGraph branches each checked the
    remaining 0.003 USD before any of them had spent it, and the run cost
    0.008 USD for a 0.005 USD budget. Calls now reserve their estimated cost
    and latency atomically.
    """

    def test_budget(self, backend: str) -> None:
        engine = RuntimeDecisionEngine()
        workflow = get_backend(backend).compile(
            plan(_fan_out(max_cost_usd=0.005)), engine=engine, executor=_SlowExecutor()
        )
        with pytest.raises(NodeAbortError):
            workflow.invoke()
        assert engine.budget_used <= 0.005 + 1e-12

    def test_latency(self, backend: str) -> None:
        engine = RuntimeDecisionEngine()
        workflow = get_backend(backend).compile(
            plan(_fan_out(max_latency_seconds=0.25)),
            engine=engine,
            executor=_SlowExecutor(),
        )
        with pytest.raises(NodeAbortError):
            workflow.invoke()
        assert engine.latency_used_ms <= 250.0 + 1e-9

    def test_subgraph_counts_the_parent_latency(self, backend: str) -> None:
        """A sub-graph used to start its latency budget from zero."""
        task = CognitiveTask(
            objective="o",
            task_constraints=TaskConstraint(max_latency_seconds=0.7),
            subtasks=[
                SubTask(id="a", objective="a", model_tier_hint="quality"),
                SubTask(
                    id="team",
                    objective="t",
                    dependencies=["a"],
                    subtasks=[
                        SubTask(id="x", objective="x", model_tier_hint="quality")
                    ],
                ),
            ],
        )
        state = get_backend(backend).compile(plan(task)).invoke()
        assert state["total_latency_ms"] <= 700.0
        models = {e["node_id"]: e["model_used"] for e in state["execution_log"]}
        assert models["a"] == "quality-pro"
        assert models["x"] != "quality-pro"  # 500 ms no longer fit


class TestReservation:
    def test_a_reserved_budget_is_not_offered_twice(self) -> None:
        engine = RuntimeDecisionEngine(budget_total=0.003)
        assert engine._try_reserve(0.002, 80.0)
        assert engine.budget_remaining == pytest.approx(0.001)
        assert not engine._try_reserve(0.002, 80.0)

    def test_failed_reservation_is_reported(self) -> None:
        engine = RuntimeDecisionEngine(budget_total=0.001)
        model = next(m for m in engine.available_models if m.name == "fast-mini")
        node = CEGNode(id="a", objective="a")
        with pytest.raises(BudgetUnavailableError):
            engine.call(node, model, MockExecutor(), {"node_outputs": {}})
        assert engine.budget_used == 0.0
