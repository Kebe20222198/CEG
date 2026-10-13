"""Experiment: the sales pipeline on real models, checked against a reference.

The sales pipeline (``analyse_ventes_alertes``) is run on each real provider
(Gemini, Groq, Ollama), on its four test scenarios, the model receiving the
CSV rows in its prompt. Every run is checked against the deterministic Python
implementation of the same pipeline, which is the reference:

  - scenarios A, B, C: the regions named in the alert must be exactly the
    regions whose sales dropped by more than 20 % (none for A);
  - scenario D (corrupted file): the reference refuses the data; a run is
    correct when it sends no false alert.

The declaration is unchanged except for its latency limit, raised to 300 s so
that a local model on a CPU can finish; the measured latency is reported.

Run:  ``python -m ceg.experiments.ventes_reelles [--runs 3] [--json out.json]``
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from statistics import mean
from typing import Any

from ceg.backends import get_backend
from ceg.llm.executor import LLMExecutor
from ceg.llm.inputs import for_real_models
from ceg.llm.providers import REAL_MODELS, available_models, load_dotenv, providers
from ceg.planner import plan
from ceg.runtime.decision_engine import RuntimeDecisionEngine
from ceg.runtime.fallback import NodeAbortError
from ceg.use_cases.sales_pipeline import (
    SalesExecutor,
    analyse_ventes_alertes,
    build_sales_graph,
)

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "backend" / "tests" / "fixtures"
SCENARIOS = {
    "A": "scenario_a_normal.csv",
    "B": "scenario_b_single_anomaly.csv",
    "C": "scenario_c_multiple_anomalies.csv",
    "D": "scenario_d_corrupted.csv",
}
REGIONS = ("Nord", "Sud", "Est", "Ouest")
LATENCY_LIMIT_S = 300.0


def reference(csv_file: str) -> set[str] | None:
    """Regions in anomaly per the Python pipeline (None: data refused)."""
    graph = build_sales_graph()
    workflow = get_backend("python").compile(graph, executor=SalesExecutor())
    try:
        state = workflow.invoke({"inputs": {"csv_path": str(FIXTURES / csv_file)}})
    except NodeAbortError:
        return None
    detection = state["node_outputs"]["detect_anomaly"]
    return {a["region"] for a in detection["anomalies"]}


def regions_in(value: Any) -> set[str]:
    """Region names mentioned in a step's output."""
    text = json.dumps(value, ensure_ascii=False)
    return {r for r in REGIONS if r in text}


def run_once(csv_file: str, expected: set[str] | None, provider: str) -> dict[str, Any]:
    declaration = analyse_ventes_alertes.declare()
    assert declaration.task_constraints is not None
    relaxed = declaration.task_constraints.model_copy(
        update={"max_latency_seconds": LATENCY_LIMIT_S}
    )
    graph = plan(declaration.model_copy(update={"task_constraints": relaxed}))
    models = available_models(REAL_MODELS, [provider])
    engine = RuntimeDecisionEngine(available_models=models, max_latency_ms=120_000.0)
    executor = LLMExecutor(graph)
    workflow = get_backend("langgraph").compile(graph, engine=engine, executor=executor)
    inputs = for_real_models({"csv_path": str(FIXTURES / csv_file)})
    try:
        state = workflow.invoke({"inputs": inputs})
        error = None
    except NodeAbortError as exc:
        state, error = {}, f"{exc.node_id}: {str(exc.reason)[:160]}"
    outputs = state.get("node_outputs", {}) if state else {}
    statuses = state.get("node_statuses", {}) if state else {}
    alerted = statuses.get("generate_alert") == "completed"
    named = regions_in(outputs.get("generate_alert")) if alerted else set()
    if expected is None:  # corrupted data: correct if no false alert
        correct = not alerted
    else:
        correct = error is None and named == expected
    return {
        "completed": error is None,
        "error": error,
        "correct": correct,
        "alerted": alerted,
        "regions": sorted(named),
        "cost_usd": round(float(state.get("total_cost", 0.0)) if state else 0.0, 6),
        "latency_ms": round(sum(c["latency_ms"] for c in executor.calls), 1),
        "failed_calls": sum(not c["ok"] for c in executor.calls),
        "errors": sorted(
            {str(c["error"])[:120] for c in executor.calls if not c["ok"]}
        ),
    }


def _provider_result(by_scenario: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    every = [r for rs in by_scenario.values() for r in rs]
    done = [r for r in every if r["completed"]]
    return {
        "available": True,
        "runs": by_scenario,
        "summary": {
            "runs": len(every),
            "correct": round(mean(float(r["correct"]) for r in every), 3)
            if every
            else None,
            "completed": round(len(done) / len(every), 3) if every else None,
            "cost_usd": round(mean(r["cost_usd"] for r in done), 6) if done else None,
            "latency_ms": round(mean(r["latency_ms"] for r in done), 1)
            if done
            else None,
            "by_scenario": {
                name: round(mean(float(r["correct"]) for r in rs), 3) if rs else None
                for name, rs in by_scenario.items()
            },
        },
    }


def run_experiment(
    *,
    runs: int = 3,
    only: list[str] | None = None,
    pause_s: float = 10.0,
    save: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run every scenario ``runs`` times per provider.

    ``save`` receives the report after every run, so that an interrupted
    experiment keeps what it has measured.
    """
    expected = {name: reference(file) for name, file in SCENARIOS.items()}
    report: dict[str, Any] = {
        "workflow": "analyse_ventes_alertes",
        "latency_limit_s": LATENCY_LIMIT_S,
        "runs_per_scenario": runs,
        "expected": {
            k: sorted(v) if v is not None else None for k, v in expected.items()
        },
        "providers": {},
    }
    for provider in only or list(providers()):
        if not available_models(REAL_MODELS, [provider]):
            report["providers"][provider] = {"available": False}
            continue
        by_scenario: dict[str, list[dict[str, Any]]] = {n: [] for n in SCENARIOS}
        for name, file in SCENARIOS.items():
            for i in range(runs):
                print(f"  {provider} · scénario {name} · {i + 1}/{runs}…", flush=True)
                by_scenario[name].append(run_once(file, expected[name], provider))
                report["providers"][provider] = _provider_result(by_scenario)
                if save is not None:
                    save(report)
                time.sleep(pause_s)  # free tiers limit tokens per minute
    return report


def _print(report: dict[str, Any]) -> None:
    print(f"\nRéférence : {report['expected']}\n")
    header = (
        f"{'FOURNISSEUR':<10}{'CORRECT':>9}{'A':>6}{'B':>6}{'C':>6}{'D':>6}"
        f"{'COÛT $':>10}{'LAT. s':>8}"
    )
    print(header)
    print("-" * len(header))
    for name, result in report["providers"].items():
        if not result.get("available"):
            print(f"{name:<10}non configuré")
            continue
        s = result["summary"]
        by = s["by_scenario"]
        cost = "—" if s["cost_usd"] is None else f"{s['cost_usd']:.4f}"
        latency = "—" if s["latency_ms"] is None else f"{s['latency_ms'] / 1000:.1f}"

        def pct(value: float | None) -> str:
            return "—" if value is None else f"{value:.0%}"

        print(
            f"{name:<10}{pct(s['correct']):>9}"
            + "".join(f"{pct(by.get(k)):>6}" for k in "ABCD")
            + f"{cost:>10}{latency:>8}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline des ventes sur modèles réels."
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--providers", help="ex. gemini,groq,ollama (défaut : tous)")
    parser.add_argument("--pause", type=float, default=10.0)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    for folder in (Path.cwd(), Path.cwd().parent, ROOT):
        load_dotenv(folder / ".env")
    logging.getLogger("ceg").setLevel(logging.CRITICAL)
    only = args.providers.split(",") if args.providers else None

    def save(report: dict[str, Any]) -> None:
        if args.json:
            text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
            args.json.write_text(text)

    report = run_experiment(runs=args.runs, only=only, pause_s=args.pause, save=save)
    _print(report)


if __name__ == "__main__":
    main()
