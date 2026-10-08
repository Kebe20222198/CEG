"""Experiment: static optimiser vs learned optimiser.

The same task runs N times in the simulated environment (``ceg.simulation``)
under two strategies:

  - **static**: models are ranked with the registry's static ratings;
  - **learned**: models are ranked with ``ModelStatistics``, which every
    call — failed or not — keeps updating across runs.

The task declares no tier hints: the optimiser alone decides. Measured per
run, from the simulator's ground truth (which the optimiser never sees):

  - **cost** and **latency** of every call, failed calls included;
  - **failed calls** (unusable answers, retried or escalated);
  - **quality** actually delivered: true quality of the model that produced
    each node's result.

Run:  ``python -m ceg.experiments.learned_optimizer [--runs 40] [--seed 0]``
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from statistics import mean
from typing import Any

from ceg.backends import get_backend
from ceg.models.task import CognitiveTask, SubTask, TaskConstraint
from ceg.planner import plan
from ceg.runtime.decision_engine import RuntimeDecisionEngine
from ceg.runtime.fallback import NodeAbortError
from ceg.runtime.statistics import ModelStatistics
from ceg.simulation import SimulatedLLMExecutor

STRATEGIES = ("static", "learned")


def benchmark_task() -> CognitiveTask:
    """A five-step analysis task with no model hints."""
    steps = [
        ("collect", "Collecter les ventes", ["data_retrieval"]),
        ("aggregate", "Agréger par région", ["aggregation", "computation"]),
        ("trend", "Calculer les tendances", ["trend_analysis", "computation"]),
        ("detect", "Détecter les anomalies", ["anomaly_detection", "reasoning"]),
        ("report", "Rédiger la synthèse", ["summarization", "text_generation"]),
    ]
    return CognitiveTask(
        name="benchmark_optimiseur",
        objective="Analyser les ventes et signaler les anomalies",
        task_constraints=TaskConstraint(
            max_cost_usd=1.0, max_latency_seconds=60.0, min_quality_score=0.0
        ),
        subtasks=[
            SubTask(
                id=step_id,
                objective=objective,
                required_capabilities=caps,
                dependencies=[steps[i - 1][0]] if i else [],
            )
            for i, (step_id, objective, caps) in enumerate(steps)
        ],
    )


@dataclass(frozen=True)
class RunMetrics:
    """Ground-truth measurements of one run."""

    cost: float
    latency_ms: float
    failed_calls: int
    quality: float
    aborted: bool
    accounted_cost: float | None
    models: dict[str, str]


def run_strategy(
    strategy: str,
    *,
    runs: int = 40,
    seed: int = 0,
    backend: str = "langgraph",
    exploration: float = 0.1,
    prior_weight: float = 3.0,
) -> tuple[list[RunMetrics], ModelStatistics | None]:
    """Run the benchmark task ``runs`` times under ``strategy``."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{strategy}'. Use one of {STRATEGIES}.")
    statistics = (
        ModelStatistics(prior_weight=prior_weight, exploration=exploration)
        if strategy == "learned"
        else None
    )
    graph = plan(benchmark_task())
    metrics: list[RunMetrics] = []
    for run in range(runs):
        executor = SimulatedLLMExecutor(graph, seed=seed * 100_000 + run)
        engine = RuntimeDecisionEngine(statistics=statistics)
        workflow = get_backend(backend).compile(graph, engine=engine, executor=executor)
        accounted: float | None
        try:
            state = workflow.invoke()
            accounted = float(state["total_cost"])
            aborted = False
        except NodeAbortError:
            accounted = None
            aborted = True

        delivered: dict[str, Any] = {}
        for call in executor.calls:
            if call.success:
                delivered[call.node_id] = call
        metrics.append(
            RunMetrics(
                cost=sum(c.cost for c in executor.calls),
                latency_ms=sum(c.latency_ms for c in executor.calls),
                failed_calls=sum(not c.success for c in executor.calls),
                quality=(
                    mean(c.true_quality for c in delivered.values())
                    if delivered
                    else 0.0
                ),
                aborted=aborted,
                accounted_cost=accounted,
                models={node: c.model for node, c in delivered.items()},
            )
        )
    return metrics, statistics


def summarize(metrics: list[RunMetrics]) -> dict[str, float]:
    """Means over a list of runs."""
    return {
        "cost_usd": round(mean(m.cost for m in metrics), 5),
        "latency_ms": round(mean(m.latency_ms for m in metrics), 1),
        "failed_calls": round(mean(m.failed_calls for m in metrics), 2),
        "quality": round(mean(m.quality for m in metrics), 4),
        "abort_rate": round(mean(float(m.aborted) for m in metrics), 3),
    }


def compare(
    *,
    runs: int = 40,
    seed: int = 0,
    backend: str = "langgraph",
    window: int = 10,
    exploration: float = 0.1,
) -> dict[str, Any]:
    """Run both strategies; summarise all runs and the last ``window`` runs."""
    results: dict[str, Any] = {}
    for strategy in STRATEGIES:
        metrics, statistics = run_strategy(
            strategy, runs=runs, seed=seed, backend=backend, exploration=exploration
        )
        results[strategy] = {
            "all_runs": summarize(metrics),
            f"last_{window}_runs": summarize(metrics[-window:]),
            "final_models": metrics[-1].models,
            "statistics": statistics.summary() if statistics else None,
        }
    return results


def _print_table(results: dict[str, Any], window: int) -> None:
    columns = ["cost_usd", "latency_ms", "failed_calls", "quality", "abort_rate"]
    header = f"{'':28}" + "".join(f"{c:>14}" for c in columns)
    print(header)
    print("-" * len(header))
    for strategy in STRATEGIES:
        for scope in ("all_runs", f"last_{window}_runs"):
            row = results[strategy][scope]
            label = f"{strategy} ({scope})"
            print(f"{label:28}" + "".join(f"{row[c]:>14}" for c in columns))
    print()
    for strategy in STRATEGIES:
        print(f"Modèles finaux ({strategy}) : {results[strategy]['final_models']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Static vs learned optimiser.")
    parser.add_argument("--runs", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--window", type=int, default=10)
    parser.add_argument("--backend", default="langgraph")
    parser.add_argument("--exploration", type=float, default=0.1)
    args = parser.parse_args()
    results = compare(
        runs=args.runs,
        seed=args.seed,
        backend=args.backend,
        window=args.window,
        exploration=args.exploration,
    )
    _print_table(results, args.window)


if __name__ == "__main__":
    main()
