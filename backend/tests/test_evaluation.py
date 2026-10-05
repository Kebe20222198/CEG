"""Tests S5 — Evaluation Engine.

Covers:
  - Criterion, JudgeVerdict, EvaluationReport, RobustnessReport models
  - MockJudgeClient: deterministic verdicts, fixed_scores, hash mode
  - EvaluationEngine.measure_cost / measure_latency
  - EvaluationEngine.measure_quality (via MockJudgeClient)
  - EvaluationEngine.compute_composite_score — formula verified by hand
  - EvaluationEngine.evaluate — full report on scenarios A and B
  - EvaluationEngine.measure_robustness — N-run with forced failures
  - Integration with the sales pipeline criteria (sales_criteria.py)
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from ceg.evaluation.engine import DEFAULT_WEIGHTS, EvaluationEngine, _clip
from ceg.evaluation.judge import JudgeClient, MockJudgeClient
from ceg.evaluation.models import (
    Criterion,
    CriterionScore,
    EvaluationReport,
    JudgeVerdict,
    NodeEvaluation,
    RobustnessReport,
)
from ceg.use_cases.sales_criteria import (
    ALL_SALES_CRITERIA,
    ALERT_GENERATION_CRITERIA,
    ANOMALY_DETECTION_CRITERIA,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ═══════════════════════════════════════════════════════════════════════
# Shared fixtures
# ═══════════════════════════════════════════════════════════════════════


def _run_pipeline(csv_filename: str) -> dict[str, Any]:
    """Run the sales pipeline on a fixture CSV and return the final state."""
    from ceg.compiler.compiler import CEGCompiler
    from ceg.use_cases.sales_pipeline import SalesExecutor, build_sales_graph

    csv_path = str(FIXTURES_DIR / csv_filename)
    graph = build_sales_graph()
    executor = SalesExecutor(csv_path=csv_path)
    workflow = CEGCompiler(executor=executor).compile(graph)
    return workflow.invoke()


def _make_minimal_state(
    *,
    cost: float = 0.01,
    latency_ms: float = 200.0,
    node_id: str = "test_node",
    status: str = "completed",
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal workflow state dict for isolated unit tests."""
    return {
        "total_cost": cost,
        "total_latency_ms": latency_ms,
        "node_outputs": {node_id: output or {"result": "ok"}},
        "node_statuses": {node_id: status},
        "execution_log": [
            {
                "node_id": node_id,
                "status": status,
                "output": output or {"result": "ok"},
                "cost": cost,
                "latency_ms": latency_ms,
                "confidence": 0.9,
                "model_used": "fast-mini",
            }
        ],
    }


def _make_criterion(name: str = "test_crit", weight: float = 1.0) -> Criterion:
    return Criterion(
        name=name,
        description=f"Test criterion {name}",
        weight=weight,
        evaluation_prompt_template="Evaluate: {output}",
    )


# ═══════════════════════════════════════════════════════════════════════
# 1. Pydantic models
# ═══════════════════════════════════════════════════════════════════════


class TestCriterionModel:
    def test_valid_criterion(self):
        c = _make_criterion("clarity", 0.5)
        assert c.name == "clarity"
        assert c.weight == 0.5

    def test_weight_must_be_positive(self):
        with pytest.raises(Exception):
            Criterion(
                name="bad",
                description="d",
                weight=0.0,
                evaluation_prompt_template="t",
            )

    def test_weight_must_be_le_one(self):
        with pytest.raises(Exception):
            Criterion(
                name="bad",
                description="d",
                weight=1.5,
                evaluation_prompt_template="t",
            )


class TestJudgeVerdictModel:
    def test_aggregate_auto_computed_from_scores(self):
        """aggregate_quality_score is computed automatically from criteria_scores."""
        verdict = JudgeVerdict(
            node_id="n1",
            criteria_scores=[
                CriterionScore(criterion_name="a", score=0.8, justification="ok"),
                CriterionScore(criterion_name="b", score=0.6, justification="ok"),
            ],
            # Leave aggregate_quality_score at default 0.0 → auto-computed
        )
        # Mean of 0.8 and 0.6 = 0.7
        assert abs(verdict.aggregate_quality_score - 0.7) < 1e-6

    def test_explicit_aggregate_not_overwritten(self):
        verdict = JudgeVerdict(
            node_id="n1",
            criteria_scores=[],
            aggregate_quality_score=0.95,
        )
        assert verdict.aggregate_quality_score == 0.95

    def test_criterion_score_passed_flag(self):
        cs = CriterionScore(criterion_name="c", score=0.3, passed=False)
        assert cs.passed is False


class TestEvaluationReportModel:
    def test_defaults_are_sane(self):
        report = EvaluationReport()
        assert report.scenario_name == "unnamed"
        assert report.quality_score == 0.0
        assert report.robustness_score == 1.0
        assert report.composite_score == 0.0
        assert report.weights == {"wc": 0.25, "wl": 0.25, "wq": 0.25, "wr": 0.25}

    def test_full_construction(self):
        report = EvaluationReport(
            scenario_name="test",
            total_cost_usd=0.02,
            total_latency_ms=300.0,
            total_latency_seconds=0.3,
            quality_score=0.85,
            robustness_score=0.95,
            composite_score=0.80,
        )
        assert report.scenario_name == "test"
        assert report.quality_score == 0.85


class TestRobustnessReportModel:
    def test_construction(self):
        r = RobustnessReport(
            scenario_name="s",
            n_runs=10,
            n_success=8,
            n_failure=2,
            success_rate=0.8,
            failure_reasons=["err1", "err2"],
        )
        assert r.success_rate == 0.8
        assert len(r.failure_reasons) == 2


# ═══════════════════════════════════════════════════════════════════════
# 2. MockJudgeClient
# ═══════════════════════════════════════════════════════════════════════


class TestMockJudgeClient:
    def test_default_score_applied_to_all_criteria(self):
        client = MockJudgeClient(default_score=0.88)
        criteria = [_make_criterion("a"), _make_criterion("b")]
        verdict = client.evaluate("node1", {"x": 1}, criteria)
        for cs in verdict.criteria_scores:
            assert cs.score == 0.88

    def test_fixed_scores_override_default(self):
        client = MockJudgeClient(
            default_score=0.5,
            fixed_scores={"precision": 0.95},
        )
        criteria = [
            _make_criterion("precision"),
            _make_criterion("recall"),
        ]
        verdict = client.evaluate("n", {}, criteria)
        score_map = {cs.criterion_name: cs.score for cs in verdict.criteria_scores}
        assert score_map["precision"] == 0.95
        assert score_map["recall"] == 0.5

    def test_hash_mode_is_deterministic(self):
        client = MockJudgeClient(use_hash=True)
        criteria = [_make_criterion("c")]
        v1 = client.evaluate("node_x", {"a": 1}, criteria)
        v2 = client.evaluate("node_x", {"b": 2}, criteria)
        # Same node + criterion → same score regardless of output content
        assert v1.criteria_scores[0].score == v2.criteria_scores[0].score

    def test_hash_mode_differs_across_nodes(self):
        client = MockJudgeClient(use_hash=True)
        criteria = [_make_criterion("c")]
        v1 = client.evaluate("node_a", {}, criteria)
        v2 = client.evaluate("node_b", {}, criteria)
        # Different nodes should (almost always) produce different scores
        # (hash collision is astronomically unlikely with MD5)
        assert v1.criteria_scores[0].score != v2.criteria_scores[0].score

    def test_hash_mode_scores_in_valid_range(self):
        client = MockJudgeClient(use_hash=True)
        criteria = [_make_criterion("c")]
        for node_id in ["a", "b", "c", "detect_anomaly", "generate_alert"]:
            verdict = client.evaluate(node_id, {}, criteria)
            score = verdict.criteria_scores[0].score
            assert 0.0 <= score <= 1.0

    def test_passed_flag_uses_threshold(self):
        client = MockJudgeClient(default_score=0.4, pass_threshold=0.5)
        criteria = [_make_criterion("c")]
        verdict = client.evaluate("n", {}, criteria)
        assert verdict.criteria_scores[0].passed is False

    def test_passed_flag_true_above_threshold(self):
        client = MockJudgeClient(default_score=0.8, pass_threshold=0.5)
        criteria = [_make_criterion("c")]
        verdict = client.evaluate("n", {}, criteria)
        assert verdict.criteria_scores[0].passed is True

    def test_aggregate_quality_is_weighted_average(self):
        """Weighted average: 0.9*0.6 + 0.5*0.4 = 0.74."""
        client = MockJudgeClient(
            fixed_scores={"high": 0.9, "low": 0.5},
        )
        criteria = [
            Criterion(name="high", description="h", weight=0.6, evaluation_prompt_template="t"),
            Criterion(name="low", description="l", weight=0.4, evaluation_prompt_template="t"),
        ]
        verdict = client.evaluate("n", {}, criteria)
        expected = (0.9 * 0.6 + 0.5 * 0.4) / (0.6 + 0.4)
        assert abs(verdict.aggregate_quality_score - expected) < 1e-5

    def test_justification_contains_node_id(self):
        client = MockJudgeClient()
        criteria = [_make_criterion("c")]
        verdict = client.evaluate("my_node", {"k": "v"}, criteria)
        assert "my_node" in verdict.criteria_scores[0].justification

    def test_no_criteria_returns_zero_aggregate(self):
        client = MockJudgeClient()
        verdict = client.evaluate("n", {}, [])
        assert verdict.aggregate_quality_score == 0.0

    def test_raw_judge_response_is_none(self):
        client = MockJudgeClient()
        verdict = client.evaluate("n", {}, [_make_criterion("c")])
        assert verdict.raw_judge_response is None

    def test_judge_client_protocol_compliance(self):
        """MockJudgeClient must satisfy the JudgeClient protocol."""
        client = MockJudgeClient()
        assert isinstance(client, JudgeClient)

    def test_invalid_default_score_raises(self):
        with pytest.raises(ValueError):
            MockJudgeClient(default_score=1.5)


# ═══════════════════════════════════════════════════════════════════════
# 3. EvaluationEngine — individual measures
# ═══════════════════════════════════════════════════════════════════════


class TestMeasureCost:
    def test_reads_total_cost(self):
        engine = EvaluationEngine()
        state = _make_minimal_state(cost=0.042)
        assert engine.measure_cost(state) == pytest.approx(0.042)

    def test_zero_cost(self):
        engine = EvaluationEngine()
        assert engine.measure_cost({}) == 0.0

    def test_cost_from_real_scenario_a(self):
        engine = EvaluationEngine()
        state = _run_pipeline("scenario_a_normal.csv")
        cost = engine.measure_cost(state)
        assert cost > 0.0
        assert cost < 0.50  # well within budget


class TestMeasureLatency:
    def test_reads_total_latency_ms(self):
        engine = EvaluationEngine()
        state = _make_minimal_state(latency_ms=350.0)
        assert engine.measure_latency(state) == pytest.approx(350.0)

    def test_zero_latency(self):
        engine = EvaluationEngine()
        assert engine.measure_latency({}) == 0.0

    def test_latency_from_real_scenario_b(self):
        engine = EvaluationEngine()
        state = _run_pipeline("scenario_b_single_anomaly.csv")
        latency = engine.measure_latency(state)
        assert latency > 0.0


class TestMeasureQuality:
    def test_returns_zero_when_no_criteria(self):
        engine = EvaluationEngine(judge=MockJudgeClient(), criteria=[])
        state = _make_minimal_state()
        score, verdicts = engine.measure_quality(state)
        assert score == 0.0
        assert verdicts == []

    def test_returns_zero_when_no_completed_nodes(self):
        engine = EvaluationEngine(
            judge=MockJudgeClient(),
            criteria=[_make_criterion()],
        )
        state = _make_minimal_state(status="skipped")
        score, verdicts = engine.measure_quality(state)
        assert score == 0.0

    def test_single_criterion_score_equals_mock_default(self):
        engine = EvaluationEngine(
            judge=MockJudgeClient(default_score=0.80),
            criteria=[_make_criterion("c1", weight=1.0)],
        )
        state = _make_minimal_state()
        score, verdicts = engine.measure_quality(state)
        assert score == pytest.approx(0.80)
        assert len(verdicts) == 1

    def test_multiple_nodes_averaged(self):
        """Quality is the mean over all judged nodes."""
        judge = MockJudgeClient(
            fixed_scores={"crit": 0.6},
        )
        engine = EvaluationEngine(
            judge=judge,
            criteria=[_make_criterion("crit", weight=1.0)],
        )
        state = {
            "total_cost": 0.01,
            "total_latency_ms": 100.0,
            "node_outputs": {"n1": {"a": 1}, "n2": {"b": 2}},
            "node_statuses": {"n1": "completed", "n2": "completed"},
            "execution_log": [
                {"node_id": "n1", "status": "completed", "output": {"a": 1},
                 "cost": 0.005, "latency_ms": 50.0, "confidence": 0.9},
                {"node_id": "n2", "status": "completed", "output": {"b": 2},
                 "cost": 0.005, "latency_ms": 50.0, "confidence": 0.9},
            ],
        }
        score, verdicts = engine.measure_quality(state)
        assert score == pytest.approx(0.6)
        assert len(verdicts) == 2

    def test_quality_node_ids_filter(self):
        """When quality_node_ids is set, only those nodes are judged."""
        engine = EvaluationEngine(
            judge=MockJudgeClient(default_score=0.9),
            criteria=[_make_criterion("c")],
            quality_node_ids=["n1"],
        )
        state = {
            "total_cost": 0.0,
            "total_latency_ms": 0.0,
            "node_outputs": {"n1": {"x": 1}, "n2": {"y": 2}},
            "node_statuses": {"n1": "completed", "n2": "completed"},
            "execution_log": [
                {"node_id": "n1", "status": "completed", "output": {}, "cost": 0, "latency_ms": 0, "confidence": 0.9},
                {"node_id": "n2", "status": "completed", "output": {}, "cost": 0, "latency_ms": 0, "confidence": 0.9},
            ],
        }
        score, verdicts = engine.measure_quality(state)
        judged_ids = [v.node_id for v in verdicts]
        assert "n1" in judged_ids
        assert "n2" not in judged_ids


# ═══════════════════════════════════════════════════════════════════════
# 4. compute_composite_score — formula verification
# ═══════════════════════════════════════════════════════════════════════


class TestComputeCompositeScore:
    """Verify the exact formula from section 7.5.2:
       score = wc*(1-cost/budget) + wl*(1-latency/max_lat) + wq*quality + wr*robustness
    """

    def _expected(
        self,
        cost: float, budget: float,
        latency: float, max_lat: float,
        quality: float,
        robustness: float,
        wc: float = 0.25, wl: float = 0.25,
        wq: float = 0.25, wr: float = 0.25,
    ) -> float:
        def clip(v: float) -> float:
            return max(0.0, min(1.0, v))
        return (
            wc * clip(1 - cost / budget)
            + wl * clip(1 - latency / max_lat)
            + wq * clip(quality)
            + wr * clip(robustness)
        )

    def test_perfect_run(self):
        """Zero cost, zero latency, perfect quality, 100% robustness → 1.0."""
        engine = EvaluationEngine()
        score = engine.compute_composite_score(0.0, 0.0, 1.0, 1.0, 0.50, 15.0)
        assert score == pytest.approx(1.0)

    def test_all_zeros(self):
        """Zero quality, zero robustness, budget/latency consumed → low score."""
        engine = EvaluationEngine()
        score = engine.compute_composite_score(0.50, 15.0, 0.0, 0.0, 0.50, 15.0)
        # cost_term=0, latency_term=0, quality_term=0, robustness_term=0
        assert score == pytest.approx(0.0)

    def test_known_values_by_hand(self):
        """Manually computed: cost=0.10, budget=0.50 → cost_term=0.80
           latency=3s, max=15s → latency_term=0.80
           quality=0.85, robustness=0.90 → all weights=0.25
           expected = 0.25*(0.80+0.80+0.85+0.90) = 0.25*3.35 = 0.8375
        """
        engine = EvaluationEngine()
        score = engine.compute_composite_score(
            cost_usd=0.10,
            latency_seconds=3.0,
            quality_score=0.85,
            robustness_score=0.90,
            max_budget_usd=0.50,
            max_latency_seconds=15.0,
        )
        expected = self._expected(0.10, 0.50, 3.0, 15.0, 0.85, 0.90)
        assert score == pytest.approx(expected, abs=1e-5)
        assert abs(score - 0.8375) < 1e-4

    def test_budget_overrun_clamped_to_zero(self):
        """Cost > max_budget → cost_term clipped to 0, not negative."""
        engine = EvaluationEngine()
        score = engine.compute_composite_score(
            cost_usd=1.0,       # 2× over budget
            latency_seconds=0.0,
            quality_score=1.0,
            robustness_score=1.0,
            max_budget_usd=0.50,
            max_latency_seconds=15.0,
        )
        # cost_term=0 (clipped), latency=1, quality=1, robustness=1
        # score = 0.25*(0+1+1+1) = 0.75
        assert score == pytest.approx(0.75)

    def test_latency_overrun_clamped_to_zero(self):
        """Latency > max → latency_term clipped to 0."""
        engine = EvaluationEngine()
        score = engine.compute_composite_score(
            cost_usd=0.0,
            latency_seconds=30.0,   # 2× over max
            quality_score=1.0,
            robustness_score=1.0,
            max_budget_usd=0.50,
            max_latency_seconds=15.0,
        )
        # cost_term=1, latency_term=0 (clipped), quality=1, robustness=1
        # score = 0.25*(1+0+1+1) = 0.75
        assert score == pytest.approx(0.75)

    def test_custom_weights(self):
        """Custom weights: wq=1.0, others=0 → score equals quality_score."""
        engine = EvaluationEngine(weights={"wc": 0.0, "wl": 0.0, "wq": 1.0, "wr": 0.0})
        score = engine.compute_composite_score(
            cost_usd=0.5, latency_seconds=15.0,
            quality_score=0.72, robustness_score=0.50,
            weights={"wc": 0.0, "wl": 0.0, "wq": 1.0, "wr": 0.0},
        )
        assert score == pytest.approx(0.72)

    def test_score_always_in_0_1(self):
        """Composite score is always in [0, 1] regardless of inputs."""
        engine = EvaluationEngine()
        for cost in [0.0, 0.25, 1.0, 10.0]:
            for latency in [0.0, 7.5, 15.0, 100.0]:
                score = engine.compute_composite_score(cost, latency, 0.5, 0.5)
                assert 0.0 <= score <= 1.0, f"score={score} for cost={cost}, lat={latency}"

    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValueError, match="sum to 1.0"):
            EvaluationEngine(weights={"wc": 0.5, "wl": 0.5, "wq": 0.5, "wr": 0.0})


class TestClipHelper:
    def test_clips_above_one(self):
        assert _clip(1.5) == 1.0

    def test_clips_below_zero(self):
        assert _clip(-0.3) == 0.0

    def test_passthrough_in_range(self):
        assert _clip(0.6) == pytest.approx(0.6)


# ═══════════════════════════════════════════════════════════════════════
# 5. EvaluationEngine.evaluate — full report
# ═══════════════════════════════════════════════════════════════════════


class TestEvaluateFullReport:
    def test_report_type(self):
        engine = EvaluationEngine(judge=MockJudgeClient(), criteria=ALL_SALES_CRITERIA)
        state = _run_pipeline("scenario_a_normal.csv")
        report = engine.evaluate(state, scenario_name="A-normal")
        assert isinstance(report, EvaluationReport)

    def test_scenario_name_set(self):
        engine = EvaluationEngine(judge=MockJudgeClient(), criteria=ALL_SALES_CRITERIA)
        state = _run_pipeline("scenario_a_normal.csv")
        report = engine.evaluate(state, scenario_name="scenario_a")
        assert report.scenario_name == "scenario_a"

    def test_cost_matches_state(self):
        engine = EvaluationEngine(judge=MockJudgeClient(), criteria=ALL_SALES_CRITERIA)
        state = _run_pipeline("scenario_a_normal.csv")
        report = engine.evaluate(state)
        assert report.total_cost_usd == pytest.approx(engine.measure_cost(state))

    def test_latency_matches_state(self):
        engine = EvaluationEngine(judge=MockJudgeClient(), criteria=ALL_SALES_CRITERIA)
        state = _run_pipeline("scenario_b_single_anomaly.csv")
        report = engine.evaluate(state)
        assert report.total_latency_ms == pytest.approx(engine.measure_latency(state))

    def test_latency_seconds_conversion(self):
        engine = EvaluationEngine()
        state = _make_minimal_state(latency_ms=3000.0)
        report = engine.evaluate(state)
        assert report.total_latency_seconds == pytest.approx(3.0)

    def test_quality_score_in_range(self):
        engine = EvaluationEngine(judge=MockJudgeClient(), criteria=ALL_SALES_CRITERIA)
        state = _run_pipeline("scenario_b_single_anomaly.csv")
        report = engine.evaluate(state)
        assert 0.0 <= report.quality_score <= 1.0

    def test_composite_score_in_range(self):
        engine = EvaluationEngine(judge=MockJudgeClient(), criteria=ALL_SALES_CRITERIA)
        state = _run_pipeline("scenario_b_single_anomaly.csv")
        report = engine.evaluate(state, max_budget_usd=0.50, max_latency_seconds=15.0)
        assert 0.0 <= report.composite_score <= 1.0

    def test_node_evaluations_count_scenario_a(self):
        """Scenario A: 5 nodes in log (4 completed + 1 skipped)."""
        engine = EvaluationEngine(judge=MockJudgeClient(), criteria=ALL_SALES_CRITERIA)
        state = _run_pipeline("scenario_a_normal.csv")
        report = engine.evaluate(state)
        assert len(report.node_evaluations) == 5

    def test_node_evaluations_count_scenario_b(self):
        """Scenario B: 5 nodes (all 5 completed including generate_alert)."""
        engine = EvaluationEngine(judge=MockJudgeClient(), criteria=ALL_SALES_CRITERIA)
        state = _run_pipeline("scenario_b_single_anomaly.csv")
        report = engine.evaluate(state)
        assert len(report.node_evaluations) == 5

    def test_skipped_node_has_no_judge_verdict_scenario_a(self):
        """In scenario A, generate_alert is skipped → no judge verdict."""
        engine = EvaluationEngine(judge=MockJudgeClient(), criteria=ALL_SALES_CRITERIA)
        state = _run_pipeline("scenario_a_normal.csv")
        report = engine.evaluate(state)
        skipped = [ne for ne in report.node_evaluations if ne.status == "skipped"]
        assert len(skipped) == 1
        assert skipped[0].node_id == "generate_alert"
        assert skipped[0].judge_verdict is None

    def test_completed_nodes_have_judge_verdicts_scenario_b(self):
        """In scenario B, all completed nodes receive a judge verdict."""
        engine = EvaluationEngine(judge=MockJudgeClient(), criteria=ALL_SALES_CRITERIA)
        state = _run_pipeline("scenario_b_single_anomaly.csv")
        report = engine.evaluate(state)
        completed = [ne for ne in report.node_evaluations if ne.status == "completed"]
        for ne in completed:
            assert ne.judge_verdict is not None

    def test_robustness_defaults_to_one(self):
        """Single-run evaluate() passes robustness_score=1.0 by default."""
        engine = EvaluationEngine()
        state = _make_minimal_state()
        report = engine.evaluate(state)
        assert report.robustness_score == 1.0

    def test_robustness_injected(self):
        engine = EvaluationEngine()
        state = _make_minimal_state()
        report = engine.evaluate(state, robustness_score=0.80)
        assert report.robustness_score == pytest.approx(0.80)

    def test_composite_formula_verified_scenario_b(self):
        """Verify composite = wc*(1-c/B) + wl*(1-l/L) + wq*q + wr*r with known values."""
        engine = EvaluationEngine(
            judge=MockJudgeClient(default_score=0.90),
            criteria=[_make_criterion("c1", weight=1.0)],
        )
        state = _run_pipeline("scenario_b_single_anomaly.csv")
        report = engine.evaluate(
            state,
            robustness_score=0.95,
            max_budget_usd=0.50,
            max_latency_seconds=15.0,
        )
        # Recompute expected from raw values
        cost_term = _clip(1.0 - report.total_cost_usd / 0.50)
        latency_term = _clip(1.0 - report.total_latency_seconds / 15.0)
        quality_term = _clip(report.quality_score)
        robustness_term = _clip(0.95)
        expected = 0.25 * (cost_term + latency_term + quality_term + robustness_term)
        assert report.composite_score == pytest.approx(expected, abs=1e-5)


# ═══════════════════════════════════════════════════════════════════════
# 6. measure_robustness — N-run success rate
# ═══════════════════════════════════════════════════════════════════════


class TestMeasureRobustness:
    def _scenario_a_factories(self):
        from ceg.use_cases.sales_pipeline import SalesExecutor, build_sales_graph
        csv = str(FIXTURES_DIR / "scenario_a_normal.csv")
        return build_sales_graph, lambda: SalesExecutor(csv_path=csv)

    def test_all_success_scenario_a(self):
        build_fn, executor_factory = self._scenario_a_factories()
        engine = EvaluationEngine()
        report = engine.measure_robustness(
            build_fn=build_fn,
            executor_factory=executor_factory,
            scenario_name="A-normal",
            n_runs=5,  # small N for fast tests
        )
        assert isinstance(report, RobustnessReport)
        assert report.n_runs == 5
        assert report.n_success == 5
        assert report.n_failure == 0
        assert report.success_rate == pytest.approx(1.0)

    def test_success_rate_computation(self):
        """With 5 runs all succeeding, success_rate = 1.0."""
        build_fn, executor_factory = self._scenario_a_factories()
        engine = EvaluationEngine()
        report = engine.measure_robustness(
            build_fn=build_fn,
            executor_factory=executor_factory,
            n_runs=5,
        )
        assert report.success_rate == report.n_success / report.n_runs

    def test_mean_cost_positive(self):
        build_fn, executor_factory = self._scenario_a_factories()
        engine = EvaluationEngine()
        report = engine.measure_robustness(build_fn=build_fn, executor_factory=executor_factory, n_runs=3)
        assert report.mean_cost_usd > 0.0

    def test_mean_latency_positive(self):
        build_fn, executor_factory = self._scenario_a_factories()
        engine = EvaluationEngine()
        report = engine.measure_robustness(build_fn=build_fn, executor_factory=executor_factory, n_runs=3)
        assert report.mean_latency_ms > 0.0

    def test_forced_failures_reduce_success_rate(self):
        """Corrupted CSV → all runs abort → success_rate = 0.0."""
        from ceg.use_cases.sales_pipeline import SalesExecutor, build_sales_graph
        csv = str(FIXTURES_DIR / "scenario_d_corrupted.csv")
        engine = EvaluationEngine()
        report = engine.measure_robustness(
            build_fn=build_sales_graph,
            executor_factory=lambda: SalesExecutor(csv_path=csv),
            scenario_name="D-corrupted",
            n_runs=3,
        )
        assert report.n_failure == 3
        assert report.n_success == 0
        assert report.success_rate == pytest.approx(0.0)
        assert len(report.failure_reasons) == 3

    def test_failure_reasons_non_empty_on_corrupted(self):
        from ceg.use_cases.sales_pipeline import SalesExecutor, build_sales_graph
        csv = str(FIXTURES_DIR / "scenario_d_corrupted.csv")
        engine = EvaluationEngine()
        report = engine.measure_robustness(
            build_fn=build_sales_graph,
            executor_factory=lambda: SalesExecutor(csv_path=csv),
            n_runs=2,
        )
        for reason in report.failure_reasons:
            assert len(reason) > 0

    def test_run_results_length(self):
        build_fn, executor_factory = self._scenario_a_factories()
        engine = EvaluationEngine()
        report = engine.measure_robustness(build_fn=build_fn, executor_factory=executor_factory, n_runs=4)
        assert len(report.run_results) == 4

    def test_partial_failure_rate(self):
        """Mix of success (scenario_a) and failure: use a flaky executor."""
        from ceg.use_cases.sales_pipeline import SalesExecutor, build_sales_graph
        from ceg.compiler.mock_executor import ExecutionError

        call_count = {"n": 0}
        csv_ok = str(FIXTURES_DIR / "scenario_a_normal.csv")
        csv_bad = str(FIXTURES_DIR / "scenario_d_corrupted.csv")

        def flaky_factory() -> SalesExecutor:
            call_count["n"] += 1
            # Alternate: odd runs use good CSV, even runs use corrupted CSV
            return SalesExecutor(csv_path=csv_ok if call_count["n"] % 2 == 1 else csv_bad)

        engine = EvaluationEngine()
        report = engine.measure_robustness(
            build_fn=build_sales_graph,
            executor_factory=flaky_factory,
            n_runs=6,
        )
        # 3 odd runs succeed (1,3,5), 3 even runs fail (2,4,6)
        assert report.n_success == 3
        assert report.n_failure == 3
        assert report.success_rate == pytest.approx(0.5)

    def test_scenario_name_in_report(self):
        build_fn, executor_factory = self._scenario_a_factories()
        engine = EvaluationEngine()
        report = engine.measure_robustness(
            build_fn=build_fn,
            executor_factory=executor_factory,
            scenario_name="my-test",
            n_runs=2,
        )
        assert report.scenario_name == "my-test"


# ═══════════════════════════════════════════════════════════════════════
# 7. Sales criteria (sales_criteria.py)
# ═══════════════════════════════════════════════════════════════════════


class TestSalesCriteria:
    def test_all_criteria_are_criterion_instances(self):
        for c in ALL_SALES_CRITERIA:
            assert isinstance(c, Criterion)

    def test_anomaly_criteria_weights_sum_to_one(self):
        total = sum(c.weight for c in ANOMALY_DETECTION_CRITERIA)
        assert abs(total - 1.0) < 1e-6

    def test_alert_criteria_weights_sum_to_one(self):
        total = sum(c.weight for c in ALERT_GENERATION_CRITERIA)
        assert abs(total - 1.0) < 1e-6

    def test_all_criteria_have_unique_names(self):
        names = [c.name for c in ALL_SALES_CRITERIA]
        assert len(names) == len(set(names))

    def test_prompt_templates_contain_output_placeholder(self):
        for c in ALL_SALES_CRITERIA:
            assert "{output}" in c.evaluation_prompt_template

    def test_judge_produces_verdict_for_all_criteria(self):
        client = MockJudgeClient(default_score=0.88)
        verdict = client.evaluate("detect_anomaly", {"anomalies_found": True}, ALL_SALES_CRITERIA)
        assert len(verdict.criteria_scores) == len(ALL_SALES_CRITERIA)


# ═══════════════════════════════════════════════════════════════════════
# 8. Integration — EvaluationReport on scenarios A and B (end-to-end)
# ═══════════════════════════════════════════════════════════════════════


class TestIntegrationScenariosAB:
    """End-to-end evaluation report generation for scenarios A and B."""

    def _engine(self) -> EvaluationEngine:
        return EvaluationEngine(
            judge=MockJudgeClient(default_score=0.90),
            criteria=ALL_SALES_CRITERIA,
        )

    def test_scenario_a_composite_score_high(self):
        """Scenario A: low cost + low latency + mock quality → high composite."""
        engine = self._engine()
        state = _run_pipeline("scenario_a_normal.csv")
        report = engine.evaluate(
            state,
            scenario_name="A-normal",
            max_budget_usd=0.50,
            max_latency_seconds=15.0,
            robustness_score=1.0,
        )
        # Sales executors cost ~0.019 USD, latency ~500ms — very efficient
        assert report.composite_score > 0.80

    def test_scenario_b_report_complete(self):
        engine = self._engine()
        state = _run_pipeline("scenario_b_single_anomaly.csv")
        report = engine.evaluate(
            state,
            scenario_name="B-single-anomaly",
            max_budget_usd=0.50,
            max_latency_seconds=15.0,
        )
        assert report.total_cost_usd > 0
        assert report.total_latency_ms > 0
        assert report.quality_score > 0
        assert report.composite_score > 0

    def test_scenario_b_quality_uses_all_criteria(self):
        """With 5 completed nodes and criteria, quality > 0."""
        engine = self._engine()
        state = _run_pipeline("scenario_b_single_anomaly.csv")
        report = engine.evaluate(state)
        assert report.quality_score > 0.0

    def test_scenario_a_skipped_node_zero_cost(self):
        """generate_alert is skipped in A → its NodeEvaluation has cost 0."""
        engine = self._engine()
        state = _run_pipeline("scenario_a_normal.csv")
        report = engine.evaluate(state)
        skipped = next(ne for ne in report.node_evaluations if ne.node_id == "generate_alert")
        assert skipped.cost_usd == 0.0

    def test_robustness_scenario_a_then_integrate(self):
        """Full S5 flow: run robustness check then inject score into evaluate."""
        from ceg.use_cases.sales_pipeline import SalesExecutor, build_sales_graph
        csv = str(FIXTURES_DIR / "scenario_a_normal.csv")
        engine = self._engine()

        rob_report = engine.measure_robustness(
            build_fn=build_sales_graph,
            executor_factory=lambda: SalesExecutor(csv_path=csv),
            scenario_name="A-robustness",
            n_runs=5,
        )
        assert rob_report.success_rate == 1.0

        state = _run_pipeline("scenario_a_normal.csv")
        eval_report = engine.evaluate(
            state,
            scenario_name="A-full",
            robustness_score=rob_report.success_rate,
        )
        assert eval_report.robustness_score == pytest.approx(1.0)
        assert eval_report.composite_score > 0.8

    def test_scenario_d_robustness_is_zero(self):
        """Corrupted data → 0% success rate → low composite when injected."""
        from ceg.use_cases.sales_pipeline import SalesExecutor, build_sales_graph
        csv_bad = str(FIXTURES_DIR / "scenario_d_corrupted.csv")
        engine = EvaluationEngine(judge=MockJudgeClient(), criteria=[])

        rob = engine.measure_robustness(
            build_fn=build_sales_graph,
            executor_factory=lambda: SalesExecutor(csv_path=csv_bad),
            n_runs=3,
        )
        assert rob.success_rate == pytest.approx(0.0)
        # Even with perfect quality, a 0% robustness pulls composite down
        score = engine.compute_composite_score(
            cost_usd=0.0,
            latency_seconds=0.0,
            quality_score=1.0,
            robustness_score=0.0,
        )
        # cost=1.0, latency=1.0, quality=1.0, robustness=0.0 → 0.25*3 = 0.75
        assert score == pytest.approx(0.75)
