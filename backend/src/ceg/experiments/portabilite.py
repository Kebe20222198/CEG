"""Experiment: the same declared task on several real model providers.

Objective OS2 and hypothesis H2 of the specification: a task runs on several
providers without any change to its declaration, with a quality gap of at
most 15 %. The task is the ticket-triage workflow (``workflows/``), unchanged
except for its latency limit, raised so that a local model on a CPU fits.

For each configured provider (Gemini, Groq, Ollama), the engine may only pick
that provider's models; each run is measured on:

  - completion, cost (list prices), latency and failed calls;
  - quality, by one LLM judge for every provider (same model, same criteria)
    scoring each step's output;
  - an objective check: the ticket "Panne totale…" must be flagged urgent, so
    that the on-call team is alerted (the ``escalader`` step runs).

Real calls are not deterministic: results vary between runs and over time.

Run:  ``python -m ceg.experiments.portabilite [--runs 3] [--json out.json]``
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from statistics import mean
from typing import Any

from ceg.backends import get_backend
from ceg.evaluation.models import Criterion
from ceg.llm.client import ChatClient, LLMError
from ceg.llm.executor import LLMExecutor, LLMJudgeClient
from ceg.llm.providers import REAL_MODELS, available_models, load_dotenv, providers
from ceg.models.graph import CEGGraph
from ceg.models.task import CognitiveTask
from ceg.planner import plan
from ceg.registry import WorkflowRegistry
from ceg.runtime.decision_engine import ModelProfile, RuntimeDecisionEngine
from ceg.runtime.fallback import NodeAbortError

ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = "tri_tickets_support"
LATENCY_LIMIT_S = 300.0
URGENT_TICKET = "T-102"

CRITERIA = [
    Criterion(
        name="pertinence",
        description="La sortie accomplit l'objectif de l'étape.",
        weight=0.5,
        evaluation_prompt_template="{output}",
    ),
    Criterion(
        name="exactitude",
        description=(
            "La sortie est exacte et cohérente avec les données d'entrée "
            "(pas d'information inventée)."
        ),
        weight=0.5,
        evaluation_prompt_template="{output}",
    ),
]

# Judge preference: a large model, the same for every provider.
JUDGES = ["groq:openai/gpt-oss-120b", "gemini:gemini-2.5-flash"]


def task() -> tuple[CognitiveTask, dict[str, Any]]:
    """The ticket-triage declaration (latency relaxed) and its inputs."""
    registry = WorkflowRegistry(folder=ROOT / "workflows").load()
    definition = registry.workflows[WORKFLOW]
    declaration = definition.declare()
    constraints = declaration.task_constraints
    assert constraints is not None
    relaxed = constraints.model_copy(update={"max_latency_seconds": LATENCY_LIMIT_S})
    return (
        declaration.model_copy(update={"task_constraints": relaxed}),
        dict(definition.default_inputs),
    )


def run_once(
    graph: CEGGraph,
    inputs: dict[str, Any],
    models: list[ModelProfile],
    judge: LLMJudgeClient | None,
    clients: dict[str, ChatClient] | None = None,
) -> dict[str, Any]:
    """One run restricted to ``models``; measured and judged."""
    executor = LLMExecutor(graph, clients=clients)
    engine = RuntimeDecisionEngine(available_models=models, max_latency_ms=120_000.0)
    workflow = get_backend("langgraph").compile(graph, engine=engine, executor=executor)
    started = time.perf_counter()
    try:
        state = workflow.invoke({"inputs": inputs})
        completed = all(
            s in ("completed", "skipped") for s in state["node_statuses"].values()
        )
        error = None
    except NodeAbortError as exc:
        state, completed, error = {}, False, f"{exc.node_id}: {exc.reason}"
    wall_s = time.perf_counter() - started

    outputs: dict[str, Any] = state.get("node_outputs", {}) if state else {}
    statuses: dict[str, str] = state.get("node_statuses", {}) if state else {}
    detection = outputs.get("detecter_urgences")
    urgent_flagged = bool(
        isinstance(detection, dict)
        and detection.get("a_des_urgences") is True
        and URGENT_TICKET
        in json.dumps([detection, outputs.get("escalader")], ensure_ascii=False)
    )
    quality = _judge(graph, outputs, statuses, judge) if completed and judge else None
    return {
        "completed": completed,
        "error": error,
        "cost_usd": round(sum(c.get("cost", 0.0) for c in _log(state)), 6),
        "latency_ms": round(sum(c["latency_ms"] for c in executor.calls), 1),
        "wall_s": round(wall_s, 2),
        "calls": len(executor.calls),
        "failed_calls": sum(not c["ok"] for c in executor.calls),
        "errors": sorted(
            {str(c["error"])[:120] for c in executor.calls if not c["ok"]}
        ),
        "escalated": statuses.get("escalader") == "completed",
        "urgent_flagged": urgent_flagged,
        "quality": quality,
        "models": {
            e["node_id"]: e.get("model_used")
            for e in _log(state)
            if e.get("status") == "completed"
        },
    }


def _log(state: dict[str, Any]) -> list[dict[str, Any]]:
    return list(state.get("execution_log", [])) if state else []


def _judge(
    graph: CEGGraph,
    outputs: dict[str, Any],
    statuses: dict[str, str],
    judge: LLMJudgeClient,
) -> float | None:
    scores = []
    for node in graph.nodes:
        if statuses.get(node.id) != "completed" or not isinstance(
            outputs.get(node.id), dict
        ):
            continue
        try:
            verdict = judge.evaluate(
                node.id, outputs[node.id], CRITERIA, node.objective
            )
        except LLMError as exc:
            logging.getLogger(__name__).warning("judge failed on %s: %s", node.id, exc)
            continue
        scores.append(verdict.aggregate_quality_score)
    return round(mean(scores), 3) if scores else None


def pick_judge() -> tuple[LLMJudgeClient | None, str | None]:
    """The first available judge model of ``JUDGES``."""
    by_name = {m.name: m for m in available_models(REAL_MODELS)}
    for name in JUDGES:
        model = by_name.get(name)
        if model and model.provider and model.provider_model:
            return (
                LLMJudgeClient(
                    providers()[model.provider].client(), model.provider_model
                ),
                name,
            )
    return None, None


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in runs if r["completed"]]
    qualities = [r["quality"] for r in done if r["quality"] is not None]
    return {
        "runs": len(runs),
        "completion": round(len(done) / len(runs), 3) if runs else 0.0,
        "cost_usd": round(mean(r["cost_usd"] for r in done), 6) if done else None,
        "latency_ms": round(mean(r["latency_ms"] for r in done), 1) if done else None,
        "failed_calls": round(mean(r["failed_calls"] for r in runs), 2) if runs else 0,
        "urgent_detected": (
            round(mean(float(r["urgent_flagged"] and r["escalated"]) for r in runs), 3)
            if runs
            else 0.0
        ),
        "quality": round(mean(qualities), 3) if qualities else None,
    }


def run_experiment(
    *, runs: int = 3, only: list[str] | None = None, pause_s: float = 5.0
) -> dict[str, Any]:
    declaration, inputs = task()
    graph = plan(declaration)
    judge, judge_name = pick_judge()
    results: dict[str, Any] = {}
    names = only or list(providers())
    for provider in names:
        models = available_models(REAL_MODELS, [provider])
        if not models:
            results[provider] = {"available": False}
            continue
        provider_runs = []
        for i in range(runs):
            print(f"  {provider} : exécution {i + 1}/{runs}…", flush=True)
            provider_runs.append(run_once(graph, inputs, models, judge))
            time.sleep(pause_s)  # free tiers limit requests per minute
        results[provider] = {
            "available": True,
            "models": [m.name for m in models],
            "summary": summarize(provider_runs),
            "runs": provider_runs,
        }
    return with_gap(
        {
            "workflow": WORKFLOW,
            "latency_limit_s": LATENCY_LIMIT_S,
            "runs_per_provider": runs,
            "judge": judge_name,
            "providers": results,
        }
    )


def with_gap(report: dict[str, Any]) -> dict[str, Any]:
    """Add the relative quality gap between providers, and the H2 verdict."""
    qualities = [
        r["summary"]["quality"]
        for r in report["providers"].values()
        if r.get("available") and r["summary"]["quality"] is not None
    ]
    gap = (
        round((max(qualities) - min(qualities)) / max(qualities), 3)
        if len(qualities) >= 2 and max(qualities) > 0
        else None
    )
    return {**report, "quality_gap": gap, "h2_met": gap is not None and gap <= 0.15}


def merge(previous: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Add the providers of ``new`` to an earlier report (same judge)."""
    if previous.get("judge") != new.get("judge"):
        raise ValueError(
            f"Juges différents ({previous.get('judge')} / {new.get('judge')}) : "
            "les qualités ne seraient pas comparables."
        )
    providers_ = {**previous["providers"]}
    for name, result in new["providers"].items():
        if result.get("available"):
            providers_[name] = result
    return with_gap({**previous, "providers": providers_})


def _print(report: dict[str, Any]) -> None:
    print(f"\nWorkflow : {report['workflow']} — juge : {report['judge'] or 'aucun'}\n")
    header = (
        f"{'FOURNISSEUR':<10}{'TERMINÉ':>9}{'COÛT $':>11}{'LAT. ms':>10}"
        f"{'ÉCHECS':>8}{'URGENCE':>9}{'QUALITÉ':>9}"
    )
    print(header)
    print("-" * len(header))
    for name, result in report["providers"].items():
        if not result.get("available"):
            print(f"{name:<10}non configuré")
            continue
        s = result["summary"]
        cost = "—" if s["cost_usd"] is None else f"{s['cost_usd']:.5f}"
        latency = "—" if s["latency_ms"] is None else f"{s['latency_ms']:.0f}"
        quality = "—" if s["quality"] is None else f"{s['quality']:.2f}"
        print(
            f"{name:<10}{s['completion']:>9.0%}{cost:>11}{latency:>10}"
            f"{s['failed_calls']:>8}{s['urgent_detected']:>9.0%}{quality:>9}"
        )
    gap = report["quality_gap"]
    if gap is not None:
        verdict = "respecté" if report["h2_met"] else "non respecté"
        print(
            f"\nÉcart de qualité entre fournisseurs : {gap:.0%} "
            f"(H2 : ≤ 15 %, {verdict})"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Même tâche, plusieurs fournisseurs.")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--providers", help="ex. gemini,groq,ollama (défaut : tous)")
    parser.add_argument("--pause", type=float, default=5.0)
    parser.add_argument(
        "--json", type=Path, help="écrire les résultats dans ce fichier"
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="ajouter les fournisseurs mesurés au fichier --json existant",
    )
    args = parser.parse_args()
    for folder in (Path.cwd(), Path.cwd().parent, ROOT):
        load_dotenv(folder / ".env")
    logging.getLogger("ceg").setLevel(logging.ERROR)
    only = args.providers.split(",") if args.providers else None
    report = run_experiment(runs=args.runs, only=only, pause_s=args.pause)
    if args.merge and args.json and args.json.exists():
        report = merge(json.loads(args.json.read_text(encoding="utf-8")), report)
    _print(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
