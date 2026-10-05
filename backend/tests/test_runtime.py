"""Tests for CEG Runtime Decision Engine — S3 test suite."""

from __future__ import annotations

from typing import Any

import pytest

from ceg.compiler.compiler import CEGCompiler
from ceg.compiler.mock_executor import MockExecutor
from ceg.models.graph import CEGEdge, CEGGraph
from ceg.models.node import CEGNode, ModelTierHint
from ceg.models.task import CognitiveTask
from ceg.runtime.decision_engine import (
    Constraint,
    ModelProfile,
    NoEligibleModelError,
    RuntimeDecisionEngine,
    SelectionWeights,
    select_model,
)
from ceg.runtime.fallback import (
    FallbackConfig,
    FallbackPolicy,
    NodeAbortError,
    apply_abort,
    apply_degradation,
    apply_escalation,
    apply_retry,
    apply_skip,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def all_caps_models() -> list[ModelProfile]:
    """Five mock models spanning fast/balanced/quality tiers."""
    caps = ["data_retrieval", "data_analysis", "anomaly_detection", "text_generation"]
    return [
        ModelProfile(
            name="fast-mini",
            tier=ModelTierHint.FAST,
            estimated_cost=0.002,
            estimated_latency_ms=80.0,
            supported_capabilities=caps,
        ),
        ModelProfile(
            name="fast-lite",
            tier=ModelTierHint.FAST,
            estimated_cost=0.004,
            estimated_latency_ms=100.0,
            supported_capabilities=caps,
        ),
        ModelProfile(
            name="balanced-standard",
            tier=ModelTierHint.BALANCED,
            estimated_cost=0.012,
            estimated_latency_ms=200.0,
            supported_capabilities=caps,
        ),
        ModelProfile(
            name="balanced-plus",
            tier=ModelTierHint.BALANCED,
            estimated_cost=0.020,
            estimated_latency_ms=280.0,
            supported_capabilities=caps,
        ),
        ModelProfile(
            name="quality-pro",
            tier=ModelTierHint.QUALITY,
            estimated_cost=0.060,
            estimated_latency_ms=500.0,
            supported_capabilities=caps,
        ),
    ]


@pytest.fixture
def base_node() -> CEGNode:
    """A typical CEGNode with standard capabilities."""
    return CEGNode(
        id="test_node",
        objective="Analyze sales data",
        required_capabilities=["data_analysis"],
    )


@pytest.fixture
def base_constraint() -> Constraint:
    """A generous constraint that most models pass."""
    return Constraint(budget_remaining=1.0, max_latency_ms=1000.0)


@pytest.fixture
def default_weights() -> SelectionWeights:
    return SelectionWeights()


@pytest.fixture
def sales_graph() -> CEGGraph:
    """4-node sequential sales pipeline."""
    return CEGGraph(
        nodes=[
            CEGNode(
                id="fetch_data",
                objective="Fetch raw sales data",
                task=CognitiveTask(objective="Retrieve data"),
                required_capabilities=["data_retrieval"],
            ),
            CEGNode(
                id="aggregate",
                objective="Aggregate sales metrics",
                dependencies=["fetch_data"],
                required_capabilities=["data_analysis"],
            ),
            CEGNode(
                id="detect_anomaly",
                objective="Detect anomalies in data",
                dependencies=["aggregate"],
                required_capabilities=["anomaly_detection"],
            ),
            CEGNode(
                id="generate_alert",
                objective="Generate alert report",
                dependencies=["detect_anomaly"],
                required_capabilities=["text_generation"],
            ),
        ],
        edges=[
            CEGEdge(source="fetch_data", target="aggregate"),
            CEGEdge(source="aggregate", target="detect_anomaly"),
            CEGEdge(source="detect_anomaly", target="generate_alert"),
        ],
    )


# ══════════════════════════════════════════════════════════════════════════════
# ModelProfile tests
# ══════════════════════════════════════════════════════════════════════════════


class TestModelProfile:
    def test_supports_all_required_capabilities(
        self, all_caps_models: list[ModelProfile]
    ) -> None:
        model = all_caps_models[0]  # fast-mini
        assert model.supports(["data_retrieval", "data_analysis"])

    def test_supports_empty_capabilities(
        self, all_caps_models: list[ModelProfile]
    ) -> None:
        assert all_caps_models[0].supports([])

    def test_does_not_support_unknown_capability(
        self, all_caps_models: list[ModelProfile]
    ) -> None:
        assert not all_caps_models[0].supports(["unknown_cap"])

    def test_quality_rating_known_capabilities(
        self, all_caps_models: list[ModelProfile]
    ) -> None:
        model = all_caps_models[4]  # quality-pro
        rating = model.quality_rating_for(["data_analysis"])
        assert 0.0 <= rating <= 1.0
        # quality-pro should score high
        fast_rating = all_caps_models[0].quality_rating_for(["data_analysis"])
        assert rating > fast_rating

    def test_quality_rating_empty_capabilities(
        self, all_caps_models: list[ModelProfile]
    ) -> None:
        assert all_caps_models[0].quality_rating_for([]) == 1.0


# ══════════════════════════════════════════════════════════════════════════════
# SelectionWeights tests
# ══════════════════════════════════════════════════════════════════════════════


class TestSelectionWeights:
    def test_default_weights_sum_to_one(self) -> None:
        w = SelectionWeights()
        assert abs(w.w1 + w.w2 + w.w3 - 1.0) < 1e-6

    def test_custom_weights_valid(self) -> None:
        w = SelectionWeights(w1=0.6, w2=0.2, w3=0.2)
        assert abs(w.w1 + w.w2 + w.w3 - 1.0) < 1e-6

    def test_weights_not_summing_to_one_raises(self) -> None:
        with pytest.raises(ValueError, match="sum to 1.0"):
            SelectionWeights(w1=0.5, w2=0.5, w3=0.5)


# ══════════════════════════════════════════════════════════════════════════════
# select_model() tests
# ══════════════════════════════════════════════════════════════════════════════


class TestSelectModel:
    def test_picks_best_scoring_model(
        self,
        base_node: CEGNode,
        base_constraint: Constraint,
        all_caps_models: list[ModelProfile],
        default_weights: SelectionWeights,
    ) -> None:
        """With default weights, fast/cost-efficient model scores highest."""
        chosen = select_model(
            base_node, base_constraint, all_caps_models, default_weights
        )
        assert chosen.name == "fast-mini"

    def test_respects_budget_constraint(
        self,
        base_node: CEGNode,
        all_caps_models: list[ModelProfile],
        default_weights: SelectionWeights,
    ) -> None:
        """Only models with cost <= budget_remaining should be candidates."""
        tight_constraint = Constraint(budget_remaining=0.005, max_latency_ms=1000.0)
        chosen = select_model(
            base_node, tight_constraint, all_caps_models, default_weights
        )
        assert chosen.estimated_cost <= 0.005

    def test_respects_latency_constraint(
        self,
        base_node: CEGNode,
        all_caps_models: list[ModelProfile],
        default_weights: SelectionWeights,
    ) -> None:
        """Only models with latency <= max_latency_ms should be candidates."""
        fast_constraint = Constraint(budget_remaining=1.0, max_latency_ms=90.0)
        chosen = select_model(
            base_node, fast_constraint, all_caps_models, default_weights
        )
        assert chosen.estimated_latency_ms <= 90.0

    def test_respects_required_capabilities(
        self,
        base_constraint: Constraint,
        default_weights: SelectionWeights,
    ) -> None:
        """Models missing required capabilities must be excluded."""
        node = CEGNode(
            id="n",
            objective="Do something exotic",
            required_capabilities=["exotic_capability"],
        )
        limited_model = ModelProfile(
            name="limited",
            tier=ModelTierHint.FAST,
            estimated_cost=0.001,
            estimated_latency_ms=50.0,
            supported_capabilities=["data_retrieval"],
        )
        with pytest.raises(NoEligibleModelError):
            select_model(node, base_constraint, [limited_model], default_weights)

    def test_no_eligible_model_error_all_over_budget(
        self,
        base_node: CEGNode,
        all_caps_models: list[ModelProfile],
        default_weights: SelectionWeights,
    ) -> None:
        """All models exceed budget → NoEligibleModelError."""
        zero_budget = Constraint(budget_remaining=0.0, max_latency_ms=1000.0)
        with pytest.raises(NoEligibleModelError) as exc_info:
            select_model(base_node, zero_budget, all_caps_models, default_weights)
        assert "test_node" in str(exc_info.value)

    def test_no_eligible_model_error_all_too_slow(
        self,
        base_node: CEGNode,
        all_caps_models: list[ModelProfile],
        default_weights: SelectionWeights,
    ) -> None:
        """All models exceed latency → NoEligibleModelError."""
        zero_latency = Constraint(budget_remaining=1.0, max_latency_ms=1.0)
        with pytest.raises(NoEligibleModelError):
            select_model(base_node, zero_latency, all_caps_models, default_weights)

    def test_quality_weight_favours_quality_tier(
        self,
        base_node: CEGNode,
        base_constraint: Constraint,
        all_caps_models: list[ModelProfile],
    ) -> None:
        """When quality weight w1 dominates, quality-pro tier wins."""
        quality_first = SelectionWeights(w1=0.999, w2=0.0005, w3=0.0005)
        chosen = select_model(
            base_node, base_constraint, all_caps_models, quality_first
        )
        assert chosen.tier == ModelTierHint.QUALITY

    def test_cost_weight_favours_fast_tier(
        self,
        base_node: CEGNode,
        base_constraint: Constraint,
        all_caps_models: list[ModelProfile],
    ) -> None:
        """With w2=0.98 (cost efficiency), a fast/cheap model should win."""
        cheap_first = SelectionWeights(w1=0.01, w2=0.98, w3=0.01)
        chosen = select_model(base_node, base_constraint, all_caps_models, cheap_first)
        assert chosen.tier == ModelTierHint.FAST


# ══════════════════════════════════════════════════════════════════════════════
# Fallback strategy tests (unit)
# ══════════════════════════════════════════════════════════════════════════════


class TestFallbackRetry:
    def test_retry_succeeds_after_failures(
        self, base_node: CEGNode, all_caps_models: list[ModelProfile]
    ) -> None:
        """Retry should succeed once max_failures is exhausted."""
        executor = MockExecutor(
            failure_nodes={"test_node"},
            max_failures={"test_node": 2},
        )
        engine = RuntimeDecisionEngine(budget_total=10.0)
        state: dict[str, Any] = {"node_outputs": {}}
        result = apply_retry(
            node=base_node,
            executor=executor,
            engine=engine,
            state=state,
            model=all_caps_models[0],
            max_retries=3,
            attempt=0,
        )
        assert result is not None
        assert result["node_statuses"]["test_node"] == "completed"

    def test_retry_exhausted_returns_none(
        self, base_node: CEGNode, all_caps_models: list[ModelProfile]
    ) -> None:
        """When all retries fail, apply_retry returns None."""
        executor = MockExecutor(
            failure_nodes={"test_node"},
            max_failures={"test_node": 99},  # will never succeed
        )
        engine = RuntimeDecisionEngine(budget_total=10.0)
        state: dict[str, Any] = {"node_outputs": {}}
        result = apply_retry(
            node=base_node,
            executor=executor,
            engine=engine,
            state=state,
            model=all_caps_models[0],
            max_retries=2,
            attempt=0,
        )
        assert result is None


class TestFallbackEscalation:
    def test_escalation_picks_higher_tier(
        self, base_node: CEGNode, all_caps_models: list[ModelProfile]
    ) -> None:
        """Escalation from fast tier should select a balanced or quality model."""
        executor = MockExecutor()
        engine = RuntimeDecisionEngine(
            budget_total=10.0,
            available_models=all_caps_models,
        )
        state: dict[str, Any] = {"node_outputs": {}}
        fast_model = next(m for m in all_caps_models if m.tier == ModelTierHint.FAST)
        result = apply_escalation(
            node=base_node,
            executor=executor,
            engine=engine,
            state=state,
            current_model=fast_model,
            weights=SelectionWeights(),
        )
        assert result is not None
        assert result["node_statuses"]["test_node"] == "completed"
        # model_used should be balanced or quality
        log = result["execution_log"][0]
        assert log["model_used"] in {
            "balanced-standard",
            "balanced-plus",
            "quality-pro",
        }

    def test_escalation_returns_none_at_top_tier(
        self, base_node: CEGNode, all_caps_models: list[ModelProfile]
    ) -> None:
        """No higher tier exists above quality → escalation returns None."""
        executor = MockExecutor()
        engine = RuntimeDecisionEngine(
            budget_total=10.0,
            available_models=all_caps_models,
        )
        state: dict[str, Any] = {"node_outputs": {}}
        quality_model = next(
            m for m in all_caps_models if m.tier == ModelTierHint.QUALITY
        )
        result = apply_escalation(
            node=base_node,
            executor=executor,
            engine=engine,
            state=state,
            current_model=quality_model,
            weights=SelectionWeights(),
        )
        assert result is None


class TestFallbackDegradation:
    def test_degradation_truncates_objective(
        self, base_node: CEGNode, all_caps_models: list[ModelProfile]
    ) -> None:
        """Degradation should succeed and flag output as degraded."""
        executor = MockExecutor()
        engine = RuntimeDecisionEngine(budget_total=10.0)
        state: dict[str, Any] = {"node_outputs": {}}
        result = apply_degradation(
            node=base_node,
            executor=executor,
            engine=engine,
            state=state,
            model=all_caps_models[0],
        )
        assert result is not None
        assert result["node_statuses"]["test_node"] == "completed"
        output = result["node_outputs"]["test_node"]
        assert output is not None
        assert output.get("degraded") is True
        assert output.get("original_objective") == base_node.objective


class TestFallbackSkip:
    def test_skip_marks_node_skipped(self, base_node: CEGNode) -> None:
        result = apply_skip(base_node)
        assert result["node_statuses"]["test_node"] == "skipped"
        assert result["node_outputs"]["test_node"] is None
        assert result["total_cost"] == 0.0
        log = result["execution_log"][0]
        assert log["status"] == "skipped"


class TestFallbackAbort:
    def test_abort_raises_node_abort_error(self, base_node: CEGNode) -> None:
        with pytest.raises(NodeAbortError) as exc_info:
            apply_abort(base_node, reason="Test abort")
        assert "test_node" in str(exc_info.value)
        assert "Test abort" in str(exc_info.value)


# ══════════════════════════════════════════════════════════════════════════════
# RuntimeDecisionEngine tests
# ══════════════════════════════════════════════════════════════════════════════


class TestRuntimeDecisionEngine:
    def test_budget_tracking(
        self, base_node: CEGNode, all_caps_models: list[ModelProfile]
    ) -> None:
        """Budget should decrease as nodes are executed."""
        engine = RuntimeDecisionEngine(
            budget_total=1.0,
            available_models=all_caps_models,
        )
        executor = MockExecutor()
        state: dict[str, Any] = {"node_outputs": {}}
        initial_budget = engine.budget_remaining
        engine.run_node(base_node, state, executor)
        assert engine.budget_remaining < initial_budget

    def test_budget_exhausted_triggers_degradation(
        self, all_caps_models: list[ModelProfile]
    ) -> None:
        """Engine attempts degradation when budget is 0.0."""
        node = CEGNode(
            id="costly_node",
            objective="A very important task",
            required_capabilities=["data_analysis"],
        )
        engine = RuntimeDecisionEngine(
            budget_total=0.0,  # no budget at all
            available_models=all_caps_models,
        )
        executor = MockExecutor()
        state: dict[str, Any] = {"node_outputs": {}}
        result = engine.run_node(node, state, executor)
        assert result["node_statuses"]["costly_node"] == "completed"
        output = result["node_outputs"]["costly_node"]
        assert output.get("degraded") is True

    def test_budget_exhausted_and_executor_failed_triggers_abort(
        self, all_caps_models: list[ModelProfile]
    ) -> None:
        """When budget is 0.0 and degradation fails, engine raises NodeAbortError."""
        node = CEGNode(
            id="costly_node",
            objective="A very important task",
            required_capabilities=["data_analysis"],
        )
        engine = RuntimeDecisionEngine(
            budget_total=0.0,  # no budget at all
            available_models=all_caps_models,
        )
        executor = MockExecutor(
            failure_nodes={"costly_node"},
            max_failures={"costly_node": 99},
        )
        state: dict[str, Any] = {"node_outputs": {}}
        with pytest.raises(NodeAbortError):
            engine.run_node(node, state, executor)

    def test_run_node_success_returns_state_update(
        self, base_node: CEGNode, all_caps_models: list[ModelProfile]
    ) -> None:
        engine = RuntimeDecisionEngine(
            budget_total=10.0,
            available_models=all_caps_models,
        )
        executor = MockExecutor()
        state: dict[str, Any] = {"node_outputs": {}}
        result = engine.run_node(base_node, state, executor)
        assert result["node_statuses"]["test_node"] == "completed"
        assert result["total_cost"] > 0
        assert result["total_latency_ms"] > 0
        log = result["execution_log"][0]
        assert log["model_used"] is not None

    def test_run_node_failure_triggers_retry(
        self, base_node: CEGNode, all_caps_models: list[ModelProfile]
    ) -> None:
        """Node that fails once should recover via Retry."""
        executor = MockExecutor(
            failure_nodes={"test_node"},
            max_failures={"test_node": 1},
        )
        engine = RuntimeDecisionEngine(
            budget_total=10.0,
            available_models=all_caps_models,
            fallback_config=FallbackConfig(
                policy=FallbackPolicy.RETRY,
                max_retries=3,
                escalation_chain=[FallbackPolicy.RETRY, FallbackPolicy.ABORT],
            ),
        )
        state: dict[str, Any] = {"node_outputs": {}}
        result = engine.run_node(base_node, state, executor)
        assert result["node_statuses"]["test_node"] == "completed"


# ══════════════════════════════════════════════════════════════════════════════
# FallbackOrchestrator chain tests
# ══════════════════════════════════════════════════════════════════════════════


class TestFallbackOrchestrator:
    def test_chain_retry_then_escalation_succeeds(
        self,
        base_node: CEGNode,
        all_caps_models: list[ModelProfile],
    ) -> None:
        """Chain [RETRY(exhausted) → ESCALATION] should succeed via escalation."""
        # Initial run + 1 retry fail; 3rd call (escalation) succeeds
        executor = MockExecutor(
            failure_nodes={"test_node"},
            max_failures={"test_node": 2},
        )
        engine = RuntimeDecisionEngine(
            budget_total=10.0,
            available_models=all_caps_models,
            fallback_config=FallbackConfig(
                policy=FallbackPolicy.RETRY,
                max_retries=1,
                escalation_chain=[
                    FallbackPolicy.RETRY,
                    FallbackPolicy.ESCALATION,
                    FallbackPolicy.ABORT,
                ],
            ),
        )
        state: dict[str, Any] = {"node_outputs": {}}
        result = engine.run_node(base_node, state, executor)
        assert result["node_statuses"]["test_node"] == "completed"

    def test_chain_retry_skip(
        self, base_node: CEGNode, all_caps_models: list[ModelProfile]
    ) -> None:
        """Chain [RETRY(exhausted) → SKIP] should mark node as skipped."""
        executor = MockExecutor(
            failure_nodes={"test_node"},
            max_failures={"test_node": 99},
        )
        engine = RuntimeDecisionEngine(
            budget_total=10.0,
            available_models=all_caps_models,
            fallback_config=FallbackConfig(
                policy=FallbackPolicy.RETRY,
                max_retries=1,
                escalation_chain=[FallbackPolicy.RETRY, FallbackPolicy.SKIP],
            ),
        )
        state: dict[str, Any] = {"node_outputs": {}}
        result = engine.run_node(base_node, state, executor)
        assert result["node_statuses"]["test_node"] == "skipped"

    def test_chain_abort_raises(
        self, base_node: CEGNode, all_caps_models: list[ModelProfile]
    ) -> None:
        """Chain ending with ABORT should raise NodeAbortError."""
        executor = MockExecutor(
            failure_nodes={"test_node"},
            max_failures={"test_node": 99},
        )
        engine = RuntimeDecisionEngine(
            budget_total=10.0,
            available_models=all_caps_models,
            fallback_config=FallbackConfig(
                policy=FallbackPolicy.RETRY,
                max_retries=1,
                escalation_chain=[FallbackPolicy.RETRY, FallbackPolicy.ABORT],
            ),
        )
        state: dict[str, Any] = {"node_outputs": {}}
        with pytest.raises(NodeAbortError):
            engine.run_node(base_node, state, executor)


# ══════════════════════════════════════════════════════════════════════════════
# End-to-end integration tests (with forced failures)
# ══════════════════════════════════════════════════════════════════════════════


class TestEndToEndWithFallback:
    def test_e2e_no_failures_all_nodes_completed(self, sales_graph: CEGGraph) -> None:
        """Baseline: all 4 nodes complete successfully with no failures."""
        engine = RuntimeDecisionEngine(budget_total=5.0)
        compiler = CEGCompiler(engine=engine)
        workflow = compiler.compile(sales_graph)
        result = workflow.invoke()
        for nid in ["fetch_data", "aggregate", "detect_anomaly", "generate_alert"]:
            assert result["node_statuses"][nid] == "completed"
        assert result["total_cost"] > 0

    def test_e2e_with_forced_failure_and_retry(self, sales_graph: CEGGraph) -> None:
        """aggregate fails once, recovers via Retry → all nodes complete."""
        executor = MockExecutor(
            failure_nodes={"aggregate"},
            max_failures={"aggregate": 1},
        )
        engine = RuntimeDecisionEngine(
            budget_total=5.0,
            fallback_config=FallbackConfig(
                policy=FallbackPolicy.RETRY,
                max_retries=3,
                escalation_chain=[FallbackPolicy.RETRY, FallbackPolicy.ABORT],
            ),
        )
        compiler = CEGCompiler(engine=engine, executor=executor)
        workflow = compiler.compile(sales_graph)
        result = workflow.invoke()
        # All nodes should still complete despite the initial failure
        for nid in ["fetch_data", "aggregate", "detect_anomaly", "generate_alert"]:
            assert result["node_statuses"][nid] == "completed", (
                f"Node '{nid}' status: {result['node_statuses'].get(nid)}"
            )

    def test_e2e_with_retry_then_escalation(self, sales_graph: CEGGraph) -> None:
        """detect_anomaly fails 3x (> max_retries=2) → Escalation → success."""
        executor = MockExecutor(
            failure_nodes={"detect_anomaly"},
            max_failures={"detect_anomaly": 3},
        )
        engine = RuntimeDecisionEngine(
            budget_total=10.0,
            fallback_config=FallbackConfig(
                policy=FallbackPolicy.RETRY,
                max_retries=2,
                escalation_chain=[
                    FallbackPolicy.RETRY,
                    FallbackPolicy.ESCALATION,
                    FallbackPolicy.ABORT,
                ],
            ),
        )
        compiler = CEGCompiler(engine=engine, executor=executor)
        workflow = compiler.compile(sales_graph)
        result = workflow.invoke()
        for nid in ["fetch_data", "aggregate", "detect_anomaly", "generate_alert"]:
            assert result["node_statuses"][nid] == "completed"

    def test_e2e_node_aborted_raises(self, sales_graph: CEGGraph) -> None:
        """If abort policy is triggered, NodeAbortError propagates."""
        executor = MockExecutor(
            failure_nodes={"aggregate"},
            max_failures={"aggregate": 99},
        )
        engine = RuntimeDecisionEngine(
            budget_total=5.0,
            fallback_config=FallbackConfig(
                policy=FallbackPolicy.ABORT,
                escalation_chain=[FallbackPolicy.ABORT],
            ),
        )
        compiler = CEGCompiler(engine=engine, executor=executor)
        workflow = compiler.compile(sales_graph)
        with pytest.raises((NodeAbortError, Exception)):
            workflow.invoke()

    def test_e2e_model_used_recorded_in_log(self, sales_graph: CEGGraph) -> None:
        """Execution log entries should include model_used field."""
        engine = RuntimeDecisionEngine(budget_total=5.0)
        compiler = CEGCompiler(engine=engine)
        workflow = compiler.compile(sales_graph)
        result = workflow.invoke()
        for entry in result["execution_log"]:
            assert "model_used" in entry
            assert entry["model_used"] is not None

    def test_e2e_cost_matches_log_sum(self, sales_graph: CEGGraph) -> None:
        """Total cost in state should equal sum of all log entry costs."""
        engine = RuntimeDecisionEngine(budget_total=5.0)
        compiler = CEGCompiler(engine=engine)
        workflow = compiler.compile(sales_graph)
        result = workflow.invoke()
        log_total = sum(e["cost"] for e in result["execution_log"])
        assert abs(result["total_cost"] - log_total) < 1e-6
