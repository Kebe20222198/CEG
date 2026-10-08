"""Tests for the learned optimiser, the simulator and the experiment."""

from __future__ import annotations

import pytest

from ceg.compiler.mock_executor import ExecutionError, ExecutionResult, MockExecutor
from ceg.experiments.learned_optimizer import (
    benchmark_task,
    compare,
    run_strategy,
    summarize,
)
from ceg.models.graph import CEGGraph
from ceg.models.node import CEGNode
from ceg.planner import plan
from ceg.runtime.decision_engine import (
    DEFAULT_MODEL_REGISTRY,
    Constraint,
    ModelProfile,
    RuntimeDecisionEngine,
    rank_models,
)
from ceg.runtime.statistics import PRIOR_SUCCESS_RATE, ModelStatistics
from ceg.simulation import SimulatedLLMExecutor, true_quality

MODELS = {m.name: m for m in DEFAULT_MODEL_REGISTRY}
ANALYSIS = ["anomaly_detection", "reasoning"]
WIDE = Constraint(budget_remaining=10.0, max_latency_ms=10_000.0)


def _observe(stats: ModelStatistics, model: str, n: int, *, success: bool) -> None:
    for _ in range(n):
        stats.observe(
            model,
            ANALYSIS,
            success=success,
            quality=0.9,
            cost=MODELS[model].estimated_cost,
            latency_ms=MODELS[model].estimated_latency_ms,
        )


class TestModelStatistics:
    def test_without_observations_the_static_values_apply(self) -> None:
        stats = ModelStatistics()
        model = MODELS["fast-mini"]
        assert stats.quality_estimate(model, ANALYSIS) == pytest.approx(
            model.quality_rating_for(ANALYSIS)
        )
        assert stats.success_rate(model, ANALYSIS) == pytest.approx(PRIOR_SUCCESS_RATE)
        assert stats.expected_cost(model, ANALYSIS) == pytest.approx(
            model.estimated_cost / PRIOR_SUCCESS_RATE
        )

    def test_estimate_moves_from_prior_to_evidence(self) -> None:
        """Bayesian average: prior worth ``prior_weight`` observations."""
        stats = ModelStatistics(prior_weight=3.0)
        model = MODELS["fast-mini"]
        prior = model.quality_rating_for(ANALYSIS)
        _observe(stats, "fast-mini", 3, success=True)
        assert stats.quality_estimate(model, ANALYSIS) == pytest.approx(
            (3 * prior + 3 * 0.9) / 6
        )

    def test_failures_lower_quality_and_raise_expected_cost(self) -> None:
        stats = ModelStatistics()
        model = MODELS["fast-mini"]
        before_cost = stats.expected_cost(model, ANALYSIS)
        _observe(stats, "fast-mini", 6, success=False)
        assert stats.quality_estimate(model, ANALYSIS) < model.quality_rating_for(
            ANALYSIS
        )
        assert stats.success_rate(model, ANALYSIS) < 0.5
        assert stats.expected_cost(model, ANALYSIS) > 2 * before_cost

    def test_exploration_bonus_favours_less_observed_models(self) -> None:
        stats = ModelStatistics(exploration=0.5)
        candidates = [MODELS["fast-mini"], MODELS["fast-lite"]]
        _observe(stats, "fast-mini", 10, success=True)
        bonus = {
            m.name: stats.ranking_quality(m, ANALYSIS, candidates)
            - stats.quality_estimate(m, ANALYSIS)
            for m in candidates
        }
        assert bonus["fast-lite"] > bonus["fast-mini"] > 0

    def test_no_exploration_means_no_bonus(self) -> None:
        stats = ModelStatistics(exploration=0.0)
        model = MODELS["fast-lite"]
        assert stats.ranking_quality(model, ANALYSIS, [model]) == pytest.approx(
            stats.quality_estimate(model, ANALYSIS)
        )

    def test_round_trip(self) -> None:
        stats = ModelStatistics(prior_weight=2.0, exploration=0.2)
        _observe(stats, "fast-mini", 4, success=False)
        _observe(stats, "quality-pro", 2, success=True)
        restored = ModelStatistics.from_dict(stats.to_dict())
        for name in ("fast-mini", "quality-pro"):
            assert restored.quality_estimate(MODELS[name], ANALYSIS) == pytest.approx(
                stats.quality_estimate(MODELS[name], ANALYSIS)
            )
        assert restored.summary() == stats.summary()

    @pytest.mark.parametrize("kwargs", [{"prior_weight": 0.0}, {"exploration": -1.0}])
    def test_invalid_parameters(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            ModelStatistics(**kwargs)


class TestLearnedRanking:
    def test_static_ranking_prefers_the_cheap_model(self) -> None:
        node = CEGNode(id="n", objective="o", required_capabilities=ANALYSIS)
        chosen = rank_models(node, WIDE, DEFAULT_MODEL_REGISTRY)[0][0]
        assert chosen.name == "fast-mini"

    def test_learned_ranking_drops_a_model_that_keeps_failing(self) -> None:
        stats = ModelStatistics()
        _observe(stats, "fast-mini", 10, success=False)
        node = CEGNode(id="n", objective="o", required_capabilities=ANALYSIS)
        chosen = rank_models(node, WIDE, DEFAULT_MODEL_REGISTRY, statistics=stats)[0][0]
        assert chosen.name != "fast-mini"


class _FailsOnceExecutor(MockExecutor):
    """First call of each node fails, and is billed."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: set[str] = set()

    def execute(
        self,
        node_id: str,
        objective: str,
        inputs: dict[str, object],
        attempt: int = 1,
        model: ModelProfile | None = None,
    ) -> ExecutionResult:
        result = super().execute(node_id, objective, inputs, attempt, model)
        if node_id not in self.seen:
            self.seen.add(node_id)
            raise ExecutionError(
                node_id, "first call fails", cost=result.cost, latency_ms=50.0
            )
        return result


class TestEngineAccounting:
    def test_failed_calls_are_billed_and_reported(self) -> None:
        engine = RuntimeDecisionEngine()
        node = CEGNode(id="n", objective="o")
        update = engine.run_node(node, {"node_outputs": {}}, _FailsOnceExecutor())
        price = MODELS["fast-mini"].estimated_cost
        assert update["total_cost"] == pytest.approx(2 * price)
        assert engine.budget_used == pytest.approx(2 * price)
        entry = update["execution_log"][0]
        assert entry["failed_calls"] == 1
        assert entry["latency_ms"] == pytest.approx(
            MODELS["fast-mini"].estimated_latency_ms + 50.0
        )

    def test_engine_learns_from_every_call(self) -> None:
        stats = ModelStatistics()
        engine = RuntimeDecisionEngine(statistics=stats)
        node = CEGNode(id="n", objective="o", required_capabilities=ANALYSIS)
        update = engine.run_node(node, {"node_outputs": {}}, _FailsOnceExecutor())
        # The failed call and the successful retry were both observed.
        assert stats.observations("fast-mini", ANALYSIS) == 2
        decision = update["execution_log"][0]["decision"]
        assert decision["quality_source"] == "learned"

    def test_static_engine_says_so(self) -> None:
        update = RuntimeDecisionEngine().run_node(
            CEGNode(id="n", objective="o"), {"node_outputs": {}}, MockExecutor()
        )
        assert update["execution_log"][0]["decision"]["quality_source"] == "static"


class TestSimulator:
    def _graph(self) -> CEGGraph:
        return CEGGraph(
            nodes=[CEGNode(id="detect", objective="d", required_capabilities=ANALYSIS)]
        )

    def test_truth_differs_from_the_static_rating(self) -> None:
        model = MODELS["fast-mini"]
        assert true_quality(model, ANALYSIS) < model.quality_rating_for(ANALYSIS)

    def test_unusable_answers_fail_and_are_billed(self) -> None:
        executor = SimulatedLLMExecutor(self._graph(), seed=1)
        with pytest.raises(ExecutionError) as exc_info:
            executor.execute("detect", "d", {}, model=MODELS["fast-mini"])
        assert exc_info.value.cost == MODELS["fast-mini"].estimated_cost
        assert executor.calls[0].success is False

    def test_draws_are_deterministic(self) -> None:
        def draws(seed: int) -> list[float]:
            executor = SimulatedLLMExecutor(self._graph(), seed=seed)
            for _ in range(3):
                executor.execute("detect", "d", {}, model=MODELS["quality-pro"])
            return [c.drawn_quality for c in executor.calls]

        assert draws(7) == draws(7)
        assert draws(7) != draws(8)


class TestExperiment:
    def test_learned_optimiser_beats_static_on_the_benchmark(self) -> None:
        static, _ = run_strategy("static", runs=20)
        learned, stats = run_strategy("learned", runs=20)
        s, lrn = summarize(static), summarize(learned)
        assert lrn["failed_calls"] < s["failed_calls"] / 2
        assert lrn["cost_usd"] < s["cost_usd"]
        assert lrn["latency_ms"] < s["latency_ms"]
        assert lrn["quality"] >= s["quality"] - 0.01
        assert stats is not None and stats.summary()

    def test_engine_cost_matches_the_simulator_bill(self) -> None:
        metrics, _ = run_strategy("static", runs=5)
        for run in metrics:
            assert run.accounted_cost == pytest.approx(run.cost)

    def test_same_results_on_every_backend(self) -> None:
        langgraph = compare(runs=8, backend="langgraph")
        python = compare(runs=8, backend="python")
        for strategy in ("static", "learned"):
            assert langgraph[strategy]["all_runs"] == python[strategy]["all_runs"]

    def test_benchmark_task_lets_the_optimiser_decide(self) -> None:
        graph = plan(benchmark_task())
        assert all(n.model_tier_hint is None for n in graph.nodes)

    def test_unknown_strategy(self) -> None:
        with pytest.raises(ValueError, match="Unknown strategy"):
            run_strategy("random")
