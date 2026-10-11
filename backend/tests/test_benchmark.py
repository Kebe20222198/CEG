"""Tests for the quality floor in model selection and the CEG benchmark."""

from __future__ import annotations

from ceg.backends.common import prepare_engine
from ceg.experiments.benchmark import FIXED_MODELS, SCENARIOS, run_benchmark
from ceg.models.node import CEGNode, ModelTierHint
from ceg.models.task import CognitiveTask, TaskConstraint
from ceg.planner import plan
from ceg.runtime.decision_engine import (
    DEFAULT_MODEL_REGISTRY,
    Constraint,
    RuntimeDecisionEngine,
    rank_models,
)
from ceg.runtime.statistics import ModelStatistics

ANALYSIS = ["anomaly_detection", "reasoning"]


def _constraint(min_quality: float) -> Constraint:
    return Constraint(
        budget_remaining=10.0, max_latency_ms=10_000.0, min_quality=min_quality
    )


def _ranked(
    node: CEGNode, min_quality: float, statistics: ModelStatistics | None = None
) -> list[str]:
    ranked = rank_models(
        node, _constraint(min_quality), DEFAULT_MODEL_REGISTRY, statistics=statistics
    )
    return [m.name for m, _ in ranked]


class TestQualityFloor:
    def test_only_models_expected_to_reach_the_floor_are_compared(self) -> None:
        node = CEGNode(id="detect", objective="o", required_capabilities=ANALYSIS)
        assert "fast-mini" in _ranked(node, 0.0)
        # Static ratings on this node: fast 0.50-0.55, balanced 0.79-0.81.
        assert set(_ranked(node, 0.75)) == {
            "balanced-standard",
            "balanced-plus",
            "quality-pro",
        }
        assert _ranked(node, 0.9) == ["quality-pro"]

    def test_best_effort_when_no_model_reaches_the_floor(self) -> None:
        node = CEGNode(id="detect", objective="o", required_capabilities=ANALYSIS)
        assert len(_ranked(node, 0.99)) == len(DEFAULT_MODEL_REGISTRY)

    def test_a_tier_hint_takes_precedence(self) -> None:
        node = CEGNode(
            id="detect",
            objective="o",
            required_capabilities=ANALYSIS,
            model_tier_hint=ModelTierHint.FAST,
        )
        assert set(_ranked(node, 0.75)) == {"fast-mini", "fast-lite"}

    def test_learned_estimates_are_used_when_available(self) -> None:
        stats = ModelStatistics()
        for _ in range(30):
            stats.observe(
                "balanced-standard",
                ANALYSIS,
                success=False,
                quality=0.0,
                cost=0.012,
                latency_ms=200.0,
            )
        node = CEGNode(id="detect", objective="o", required_capabilities=ANALYSIS)
        assert "balanced-standard" not in _ranked(node, 0.75, statistics=stats)

    def test_the_declaration_sets_the_engine_floor(self) -> None:
        task = CognitiveTask(
            objective="o",
            task_constraints=TaskConstraint(min_quality_score=0.8),
        )
        engine = prepare_engine(plan(task), RuntimeDecisionEngine())
        assert engine.min_quality == 0.8
        assert engine.default_constraint.min_quality == 0.8
        # A stricter engine stays stricter.
        strict = prepare_engine(plan(task), RuntimeDecisionEngine(min_quality=0.9))
        assert strict.min_quality == 0.9


class TestBenchmark:
    def test_ceg_meets_every_scenario_and_no_fixed_model_does(self) -> None:
        report = run_benchmark(runs=4)
        results = report["results"]
        assert set(results) == set(SCENARIOS)
        for scenario in SCENARIOS:
            for strategy in ("ceg:static", "ceg:learned"):
                row = results[scenario][strategy]
                assert row["respect"] == 1.0, (scenario, strategy)
                assert row["budget_overrun"] == 0.0
                assert row["latency_overrun"] == 0.0
        for model in FIXED_MODELS:
            assert any(
                results[s][f"fixe:{model}"]["respect"] < 1.0 for s in SCENARIOS
            ), model

    def test_a_fixed_model_overruns_where_ceg_does_not(self) -> None:
        results = run_benchmark(runs=2)["results"]
        assert results["serre"]["fixe:quality-pro"]["budget_overrun"] == 1.0
        assert results["serre"]["ceg:static"]["budget_overrun"] == 0.0
