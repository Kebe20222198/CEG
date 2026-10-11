"""Benchmark: CEG vs hand-written workflows with a fixed model.

Question answered: what does CEG bring compared with the usual way of
writing an LLM workflow — every step calls one model chosen by the developer,
retried on failure, with no notion of budget?

The same five-step task (``learned_optimizer.benchmark_task``, no model
hints) runs under four constraint scenarios — from cheap and fast to
demanding on quality, so that no single model suits them all — in the
simulated environment (``ceg.simulation``), with five strategies:

  - ``fixe:<model>``: hand-written workflow, every step on ``<model>``
    (the cheapest, the middle one and the best of the registry), each call
    retried up to ``MAX_ATTEMPTS`` times;
  - ``ceg:static``: CEG, models ranked with the registry's static ratings;
  - ``ceg:learned``: CEG with the learned optimiser, statistics kept across
    the runs of a scenario.

Measured per run, from the simulator's ground truth: cost and latency of
every call (failed ones included), whether the run delivered every step,
and the true quality delivered. A run *meets its constraints* when it
completed within the budget, within the latency limit and with a quality
at least ``min_quality_score``. An overrun (spending more than declared)
is counted separately from an abort (CEG stopping rather than overrunning).

Run:  ``python -m ceg.experiments.benchmark [--runs 40] [--seed 0] [--json out.json]``
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from ceg.backends import get_backend
from ceg.compiler.mock_executor import ExecutionError
from ceg.experiments.learned_optimizer import benchmark_task
from ceg.models.graph import CEGGraph
from ceg.models.task import TaskConstraint
from ceg.planner import plan
from ceg.runtime.decision_engine import DEFAULT_MODEL_REGISTRY, RuntimeDecisionEngine
from ceg.runtime.fallback import NodeAbortError
from ceg.runtime.statistics import ModelStatistics
from ceg.simulation import SimulatedLLMExecutor

MAX_ATTEMPTS = 3

SCENARIOS: dict[str, TaskConstraint] = {
    "large": TaskConstraint(
        max_cost_usd=1.0, max_latency_seconds=10.0, min_quality_score=0.75
    ),
    "moyen": TaskConstraint(
        max_cost_usd=0.15, max_latency_seconds=2.0, min_quality_score=0.75
    ),
    "serre": TaskConstraint(
        max_cost_usd=0.06, max_latency_seconds=1.0, min_quality_score=0.75
    ),
    "exigeant": TaskConstraint(
        max_cost_usd=1.0, max_latency_seconds=5.0, min_quality_score=0.90
    ),
}

FIXED_MODELS = ("fast-mini", "balanced-standard", "quality-pro")
STRATEGIES = (*(f"fixe:{m}" for m in FIXED_MODELS), "ceg:static", "ceg:learned")


@dataclass(frozen=True)
class Run:
    """Ground-truth measurements of one run."""

    completed: bool
    cost: float
    latency_ms: float
    failed_calls: int
    quality: float

    def meets(self, constraints: TaskConstraint) -> bool:
        return (
            self.completed
            and self.cost <= constraints.max_cost_usd + 1e-9
            and self.latency_ms <= constraints.max_latency_seconds * 1000 + 1e-6
            and self.quality >= constraints.min_quality_score
        )


def _measure(executor: SimulatedLLMExecutor, graph: CEGGraph, completed: bool) -> Run:
    delivered = {c.node_id: c for c in executor.calls if c.success}
    return Run(
        completed=completed and len(delivered) == len(graph.nodes),
        cost=sum(c.cost for c in executor.calls),
        latency_ms=sum(c.latency_ms for c in executor.calls),
        failed_calls=sum(not c.success for c in executor.calls),
        # A step that was never delivered counts as quality 0.
        quality=sum(c.true_quality for c in delivered.values()) / len(graph.nodes),
    )


def run_fixed(graph: CEGGraph, model_name: str, executor: SimulatedLLMExecutor) -> Run:
    """A hand-written workflow: each step, in order, on one model."""
    model = next(m for m in DEFAULT_MODEL_REGISTRY if m.name == model_name)
    for node in graph.nodes:  # the benchmark task is a chain, planned in order
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                executor.execute(node.id, node.objective, {}, attempt, model)
                break
            except ExecutionError:
                continue
        else:
            return _measure(executor, graph, completed=False)
    return _measure(executor, graph, completed=True)


def run_ceg(
    graph: CEGGraph,
    executor: SimulatedLLMExecutor,
    statistics: ModelStatistics | None,
    backend: str,
) -> Run:
    """CEG: the plan's constraints bound the engine, which picks the models."""
    engine = RuntimeDecisionEngine(statistics=statistics)
    workflow = get_backend(backend).compile(graph, engine=engine, executor=executor)
    try:
        workflow.invoke()
    except NodeAbortError:
        return _measure(executor, graph, completed=False)
    return _measure(executor, graph, completed=True)


def run_scenario(
    constraints: TaskConstraint,
    strategy: str,
    *,
    runs: int = 40,
    seed: int = 0,
    backend: str = "langgraph",
) -> list[Run]:
    """Run the benchmark task ``runs`` times under one strategy."""
    task = benchmark_task().model_copy(update={"task_constraints": constraints})
    graph = plan(task)
    statistics = ModelStatistics() if strategy == "ceg:learned" else None
    results: list[Run] = []
    for i in range(runs):
        executor = SimulatedLLMExecutor(graph, seed=seed * 100_000 + i)
        if strategy.startswith("fixe:"):
            results.append(run_fixed(graph, strategy.split(":", 1)[1], executor))
        else:
            results.append(run_ceg(graph, executor, statistics, backend))
    return results


def summarize(runs: list[Run], constraints: TaskConstraint) -> dict[str, float]:
    """Rates and means over the runs of one (scenario, strategy)."""
    budget = constraints.max_cost_usd + 1e-9
    latency = constraints.max_latency_seconds * 1000 + 1e-6
    return {
        "respect": round(mean(float(r.meets(constraints)) for r in runs), 3),
        "budget_overrun": round(mean(float(r.cost > budget) for r in runs), 3),
        "latency_overrun": round(mean(float(r.latency_ms > latency) for r in runs), 3),
        "incomplete": round(mean(float(not r.completed) for r in runs), 3),
        "cost_usd": round(mean(r.cost for r in runs), 4),
        "latency_ms": round(mean(r.latency_ms for r in runs), 1),
        "failed_calls": round(mean(r.failed_calls for r in runs), 2),
        "quality": round(mean(r.quality for r in runs), 3),
    }


def run_benchmark(
    *, runs: int = 40, seed: int = 0, backend: str = "langgraph"
) -> dict[str, Any]:
    """Every strategy on every scenario: ``{scenario: {strategy: summary}}``."""
    results: dict[str, Any] = defaultdict(dict)
    for name, constraints in SCENARIOS.items():
        for strategy in STRATEGIES:
            runs_ = run_scenario(
                constraints, strategy, runs=runs, seed=seed, backend=backend
            )
            results[name][strategy] = summarize(runs_, constraints)
    return {
        "runs": runs,
        "seed": seed,
        "backend": backend,
        "scenarios": {
            name: {
                "max_cost_usd": c.max_cost_usd,
                "max_latency_seconds": c.max_latency_seconds,
                "min_quality_score": c.min_quality_score,
            }
            for name, c in SCENARIOS.items()
        },
        "results": dict(results),
    }


def _print_table(report: dict[str, Any]) -> None:
    columns = [
        ("respect", "respect"),
        ("budget_overrun", "dépass. $"),
        ("latency_overrun", "dépass. ms"),
        ("incomplete", "inachevé"),
        ("cost_usd", "coût $"),
        ("latency_ms", "latence ms"),
        ("quality", "qualité"),
    ]
    for name, results in report["results"].items():
        limits = report["scenarios"][name]
        print(
            f"\nScénario « {name} » : budget {limits['max_cost_usd']} $, "
            f"latence {limits['max_latency_seconds']} s, "
            f"qualité ≥ {limits['min_quality_score']}"
        )
        header = f"{'':26}" + "".join(f"{label:>12}" for _, label in columns)
        print(header)
        print("-" * len(header))
        for strategy, row in results.items():
            print(f"{strategy:26}" + "".join(f"{row[key]:>12}" for key, _ in columns))


def main() -> None:
    parser = argparse.ArgumentParser(description="CEG vs fixed-model workflows.")
    parser.add_argument("--runs", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--backend", default="langgraph")
    parser.add_argument("--json", type=Path, help="write the results to this file")
    args = parser.parse_args()
    # Retries and escalations are expected here: keep the output to the table.
    logging.getLogger("ceg").setLevel(logging.ERROR)
    report = run_benchmark(runs=args.runs, seed=args.seed, backend=args.backend)
    _print_table(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
