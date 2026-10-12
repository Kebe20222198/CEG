"""The ``ceg`` command: use CEG from a terminal, like the ``airflow`` command.

    ceg list                      workflows found (and files in error)
    ceg validate <workflow>       plan and check a workflow, without running it
    ceg show <workflow>           the workflow as Python code
    ceg run <workflow>            run it here and print the trace
    ceg run <workflow> --llm all  run it on real models (Gemini, Groq, Ollama)
    ceg llm check                 check API keys, providers and served models
    ceg studio                    start the API and the Studio

API keys are read from the environment or from a ``.env`` file in the current
directory or its parent (Git ignores it).

Workflows are read from ``--workflows`` (default: ``$CEG_WORKFLOWS_DIR``, or
``./workflows``), plus the examples shipped with CEG.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ceg.backends import DEFAULT_BACKEND, available_backends, get_backend
from ceg.backends.common import hitl_node_ids
from ceg.codegen import task_to_python
from ceg.evaluation.engine import EvaluationEngine
from ceg.explain import PlanEstimate, explain
from ceg.models.graph import CEGGraph, EdgeType
from ceg.planner import PlanningError, plan
from ceg.registry import WorkflowDefinition, WorkflowRegistry
from ceg.runtime.decision_engine import RuntimeDecisionEngine
from ceg.runtime.fallback import NodeAbortError
from ceg.runtime.statistics import ModelStatistics


def main(argv: list[str] | None = None) -> int:
    """Entry point of the ``ceg`` command; returns the exit code."""
    from ceg.llm.providers import load_dotenv

    for folder in (Path.cwd(), Path.cwd().parent):
        load_dotenv(folder / ".env")
    parser = _parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    result: int = args.handler(args)
    return result


# ── Commands ──────────────────────────────────────────────────────────────────


def _cmd_list(args: argparse.Namespace) -> int:
    registry = _registry(args)
    print(f"Dossier des workflows : {registry.folder}\n")
    rows = []
    for wf_id, definition in sorted(registry.workflows.items()):
        try:
            graph = plan(definition())
            steps = str(len(graph.nodes))
        except PlanningError:
            steps = "invalide"
        rows.append([wf_id, steps, _short_path(definition.source_file)])
    _table(["WORKFLOW", "ÉTAPES", "FICHIER"], rows)
    _print_errors(registry)
    return 1 if registry.errors else 0


def _cmd_validate(args: argparse.Namespace) -> int:
    definition = _definition(args)
    if definition is None:
        return 1
    declaration = definition()
    try:
        graph = plan(declaration)
    except PlanningError as exc:
        print(f"✗ Déclaration invalide : {exc}")
        return 1

    print(f"✓ {definition.id} : déclaration valide\n")
    _describe_plan(graph)
    constraints = declaration.task_constraints
    if constraints is not None:
        print(
            f"\nContraintes : budget ≤ {constraints.max_cost_usd} $, "
            f"latence ≤ {constraints.max_latency_seconds} s, "
            f"qualité ≥ {constraints.min_quality_score}"
        )
    if declaration.tools_allowed:
        print(f"Outils autorisés : {', '.join(declaration.tools_allowed)}")

    estimate = explain(graph)
    _print_estimate(estimate)
    if not estimate.feasible:
        return 1

    print("\nBackends :")
    accepted = 0
    for backend in available_backends():
        try:
            _compile(backend.name, graph, definition)
        except ValueError as exc:
            print(f"  ✗ {backend.name} : {exc}")
            continue
        accepted += 1
        print(f"  ✓ {backend.name}")
    return 0 if accepted else 1


def _cmd_show(args: argparse.Namespace) -> int:
    definition = _definition(args)
    if definition is None:
        return 1
    print(task_to_python(definition()), end="")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    definition = _definition(args)
    if definition is None:
        return 1
    declaration = definition()
    try:
        graph = plan(declaration)
    except PlanningError as exc:
        print(f"✗ Déclaration invalide : {exc}")
        return 1

    inputs = {**definition.default_inputs, **_parse_inputs(args.input)}
    if definition.uses_csv and "csv_path" not in inputs:
        print("✗ Ce workflow lit un CSV : ajoutez --input csv_path=chemin/vers.csv")
        return 1

    statistics = _load_statistics(args.stats) if args.optimizer == "learned" else None
    llm_models = _llm_models(args)
    if llm_models == []:
        print("✗ Aucun modèle réel disponible : lancez « ceg llm check ».")
        return 1

    def new_engine() -> RuntimeDecisionEngine:
        if llm_models is None:
            return RuntimeDecisionEngine(statistics=statistics)
        # Real calls take seconds: the per-call limit is the task's latency.
        return RuntimeDecisionEngine(
            statistics=statistics,
            available_models=llm_models,
            max_latency_ms=120_000.0,
        )

    estimate = explain(graph, new_engine())
    if not estimate.feasible:
        print("✗ Plan refusé avant exécution :")
        for error in estimate.errors:
            print(f"  {error}")
        return 1
    engine = new_engine()
    executor = None
    if llm_models is not None:
        from ceg.llm.executor import LLMExecutor

        executor = LLMExecutor(graph)
    try:
        workflow = _compile(
            args.backend, graph, definition, engine=engine, executor=executor
        )
    except ValueError as exc:
        print(f"✗ {exc}")
        return 1

    models = (
        f"modèles réels : {', '.join(m.name for m in llm_models)}"
        if llm_models is not None
        else "modèles simulés"
    )
    print(
        f"▶ {definition.id} — backend {args.backend}, optimiseur {args.optimizer}, "
        f"{models}\n"
    )
    aborted: NodeAbortError | None = None
    state: dict[str, Any] = {}
    try:
        state = workflow.invoke({"inputs": inputs}, thread_id="cli")
        while state.get("__interrupt__"):
            for pending in state["__interrupt__"]:
                approved = _approve(pending.value, args.approve)
                state = workflow.resume("cli", value={"approved": approved})
    except NodeAbortError as exc:
        aborted = exc
        state = dict(workflow.get_state("cli").values)

    _print_trace(state)
    if statistics is not None:
        _save_statistics(statistics, args.stats)
    if aborted is not None:
        print(f"\n✗ Abandon sur « {aborted.node_id} » : {aborted.reason}")
        return 1

    report = EvaluationEngine(criteria=definition.criteria).evaluate(
        state, constraints=declaration.task_constraints
    )
    if report.constraint_violations:
        print(
            "\n✗ Contraintes non respectées : "
            + "; ".join(report.constraint_violations)
        )
        return 1
    print("\n✓ Terminé, contraintes respectées.")
    return 0


def _cmd_studio(args: argparse.Namespace) -> int:
    backend_dir = Path(__file__).resolve().parents[2]
    if not (backend_dir / "api" / "main.py").is_file():
        print(
            "✗ « ceg studio » se lance depuis le dépôt CEG (dossier api/ introuvable)."
        )
        return 1
    if args.workflows:
        os.environ["CEG_WORKFLOWS_DIR"] = str(Path(args.workflows).resolve())
    sys.path.insert(0, str(backend_dir))

    frontend_dir = backend_dir.parent / "frontend"
    frontend: subprocess.Popen[bytes] | None = None
    if not args.no_frontend and (frontend_dir / "node_modules").is_dir():
        frontend = subprocess.Popen(["npm", "run", "dev"], cwd=frontend_dir)
        print("▶ Studio : http://localhost:5173")
    print(f"▶ API    : http://localhost:{args.port}/docs")

    import uvicorn

    try:
        uvicorn.run("api.main:app", host="127.0.0.1", port=args.port)
    finally:
        if frontend is not None:
            frontend.terminate()
    return 0


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ceg", description="Cognitive Execution Graph — ligne de commande."
    )
    sub = parser.add_subparsers(title="commandes")

    def command(name: str, help_text: str, handler: Any) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument(
            "--workflows",
            help="dossier des workflows (défaut : $CEG_WORKFLOWS_DIR ou ./workflows)",
        )
        p.set_defaults(handler=handler)
        return p

    command("list", "lister les workflows", _cmd_list)
    command(
        "validate", "planifier et vérifier un workflow", _cmd_validate
    ).add_argument("workflow")
    command("show", "afficher un workflow sous forme de code", _cmd_show).add_argument(
        "workflow"
    )

    run = command("run", "exécuter un workflow dans le terminal", _cmd_run)
    run.add_argument("workflow")
    run.add_argument(
        "--backend",
        default=DEFAULT_BACKEND,
        choices=[b.name for b in available_backends()],
    )
    run.add_argument("--optimizer", default="static", choices=["static", "learned"])
    run.add_argument(
        "--stats",
        default="model_statistics.json",
        help="fichier des statistiques de l'optimiseur appris",
    )
    run.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="CLÉ=VALEUR",
        help="entrée du workflow (VALEUR en JSON, ou @fichier.json) ; répétable",
    )
    run.add_argument(
        "--approve",
        default="ask",
        choices=["ask", "yes", "no"],
        help="réponse aux demandes d'approbation humaine",
    )

    run.add_argument(
        "--llm",
        metavar="FOURNISSEURS",
        help="exécuter sur de vrais modèles : all, ou gemini,groq,ollama",
    )
    run.add_argument("--models", help="fichier JSON des modèles réels (optionnel)")

    llm = sub.add_parser("llm", help="modèles réels (Gemini, Groq, Ollama)")
    llm_sub = llm.add_subparsers(title="commandes")
    check = llm_sub.add_parser(
        "check", help="vérifier les clés, les fournisseurs et les modèles servis"
    )
    check.add_argument("--models", help="fichier JSON des modèles réels (optionnel)")
    check.add_argument("--verbose", "-v", action="store_true")
    check.set_defaults(handler=_cmd_llm_check)

    studio = command("studio", "lancer l'API et le Studio", _cmd_studio)
    studio.add_argument("--port", type=int, default=8000)
    studio.add_argument("--no-frontend", action="store_true")
    return parser


def _registry(args: argparse.Namespace) -> WorkflowRegistry:
    folder = args.workflows or os.getenv("CEG_WORKFLOWS_DIR") or "workflows"
    return WorkflowRegistry(folder=Path(folder).resolve()).load()


def _definition(args: argparse.Namespace) -> WorkflowDefinition | None:
    registry = _registry(args)
    definition = registry.get(args.workflow)
    if definition is None:
        print(f"✗ Workflow inconnu : {args.workflow}")
        print(f"  Disponibles : {', '.join(sorted(registry.workflows))}")
        _print_errors(registry)
    return definition


def _compile(
    backend_name: str,
    graph: CEGGraph,
    definition: WorkflowDefinition,
    engine: RuntimeDecisionEngine | None = None,
    executor: Any = None,
) -> Any:
    from langgraph.checkpoint.memory import MemorySaver

    backend = get_backend(backend_name)
    return backend.compile(
        graph,
        engine=engine,
        executor=executor if executor is not None else definition.make_executor(),
        checkpointer=MemorySaver() if backend.supports_hitl else None,
    )


def _llm_models(args: argparse.Namespace) -> list[Any] | None:
    """Real models for ``--llm`` (None: simulated models)."""
    if not getattr(args, "llm", None):
        return None
    from ceg.llm.providers import available_models, load_models

    only = None if args.llm == "all" else [p.strip() for p in args.llm.split(",")]
    models = load_models(args.models) if getattr(args, "models", None) else None
    return available_models(models, only)


def _cmd_llm_check(args: argparse.Namespace) -> int:
    from ceg.llm.providers import REAL_MODELS, check_provider, load_models, providers

    models = load_models(args.models) if args.models else REAL_MODELS
    ok_any = False
    for provider in providers().values():
        reachable, served, message = check_provider(provider)
        mark = "✓" if reachable else "✗"
        print(f"{mark} {provider.name:<7} {provider.base_url}  —  {message}")
        ok_any = ok_any or reachable
        for model in (m for m in models if m.provider == provider.name):
            if not reachable:
                state = "—"
            elif model.provider_model in served:
                state = "✓ servi"
            else:
                state = "✗ non servi (mettre à jour l'identifiant)"
            print(f"    {model.tier.value:<8} {model.provider_model:<28} {state}")
        if reachable and args.verbose:
            print("    modèles servis : " + ", ".join(served))
    return 0 if ok_any else 1


def _describe_plan(graph: CEGGraph) -> None:
    kinds = {kind: 0 for kind in EdgeType}
    for edge in graph.edges:
        kinds[edge.edge_type] += 1
    print(f"Plan : {len(graph.nodes)} étapes")
    for node in graph.nodes:
        after = f" ← {', '.join(node.dependencies)}" if node.dependencies else ""
        hint = f" [{node.model_tier_hint.value}]" if node.model_tier_hint else ""
        team = " (équipe)" if node.subgraph is not None else ""
        print(f"  • {node.id}{hint}{team}{after}")
    details = [
        f"{kinds[EdgeType.PARALLEL]} parallèles",
        f"{kinds[EdgeType.CONDITIONAL]} conditionnelles",
        f"{kinds[EdgeType.LOOP]} boucles",
    ]
    print(f"Arêtes : {len(graph.edges)} ({', '.join(details)})")
    approvals = hitl_node_ids(graph)
    if approvals:
        print(f"Approbation humaine : {', '.join(approvals)}")


def _print_estimate(estimate: PlanEstimate) -> None:
    print("\nEXPLAIN — modèle le moins cher que le moteur peut choisir :")
    rows = []
    for step in estimate.steps:
        name = step.node_id + (" (si condition)" if step.optional else "")
        if step.team:
            rows.append(
                [name, "équipe", f"{step.cost:.4f}", f"{step.latency_ms:.0f}", ""]
            )
        else:
            quality = "" if step.quality is None else f"{step.quality:.2f}"
            rows.append(
                [
                    name,
                    step.model or "—",
                    f"{step.cost:.4f}",
                    f"{step.latency_ms:.0f}",
                    quality,
                ]
            )
    _table(["ÉTAPE", "MODÈLE", "COÛT $", "LAT. ms", "QUALITÉ"], rows)
    limit = (
        f"{estimate.max_latency_ms:.0f} ms"
        if estimate.max_latency_ms is not None
        else "sans limite"
    )
    print(
        f"\nMinimum : {estimate.min_cost:.4f} $ (budget {estimate.max_cost_usd} $), "
        f"{estimate.min_latency_ms:.0f} ms (limite {limit})"
    )
    for warning in estimate.warnings:
        print(f"⚠ {warning}")
    for error in estimate.errors:
        print(f"✗ {error}")
    if estimate.feasible:
        print("✓ Plan faisable dans les limites déclarées.")


def _parse_inputs(pairs: list[str]) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise SystemExit(f"--input attend CLÉ=VALEUR, reçu : {pair}")
        if raw.startswith("@"):
            raw = Path(raw[1:]).read_text(encoding="utf-8")
        try:
            inputs[key] = json.loads(raw)
        except json.JSONDecodeError:
            inputs[key] = raw
    return inputs


def _approve(request: dict[str, Any], policy: str) -> bool:
    print(f"⏸  {request.get('message', 'Approbation requise')}")
    if policy != "ask":
        print(
            f"   → {'approuvé' if policy == 'yes' else 'refusé'} (--approve {policy})"
        )
        return policy == "yes"
    answer = input("   Approuver ? [o/N] ").strip().lower()
    return answer in ("o", "oui", "y", "yes")


def _print_trace(state: dict[str, Any]) -> None:
    rows = []
    for entry in state.get("execution_log", []):
        if entry.get("subgraph_parent"):
            node = f"  {entry['subgraph_parent']} › {entry['node_id']}"
        else:
            node = entry["node_id"]
        fallbacks = ", ".join(entry.get("fallbacks_triggered") or []) or "-"
        rows.append(
            [
                node,
                entry.get("status", ""),
                entry.get("model_used") or "-",
                f"{entry.get('cost', 0.0):.4f}",
                f"{entry.get('latency_ms', 0.0):.0f}",
                fallbacks,
            ]
        )
    _table(["NŒUD", "STATUT", "MODÈLE", "COÛT $", "LAT. ms", "FALLBACKS"], rows)
    print(
        f"\nTotal : {state.get('total_cost', 0.0):.4f} $, "
        f"{state.get('total_latency_ms', 0.0):.0f} ms"
    )


def _table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows)]
    for line in [headers, *rows]:
        print("  ".join(str(cell).ljust(w) for cell, w in zip(line, widths)).rstrip())


def _print_errors(registry: WorkflowRegistry) -> None:
    for path, error in sorted(registry.errors.items()):
        last = error.strip().splitlines()[-1] if error.strip() else error
        print(f"\n✗ Fichier en erreur : {_short_path(path)}\n  {last}")


def _short_path(path: str | None) -> str:
    if path is None:
        return "-"
    try:
        return str(Path(path).resolve().relative_to(Path.cwd()))
    except ValueError:
        return path


def _load_statistics(path: str) -> ModelStatistics:
    file = Path(path)
    if file.is_file():
        return ModelStatistics.from_dict(json.loads(file.read_text()))
    return ModelStatistics(exploration=0.1)


def _save_statistics(statistics: ModelStatistics, path: str) -> None:
    Path(path).write_text(json.dumps(statistics.to_dict(), indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
