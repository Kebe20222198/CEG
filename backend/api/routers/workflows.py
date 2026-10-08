"""Workflows: list the platform's workflows and show their code.

Like Airflow's DAG list and Code view: every task is a workflow, listed with
its constraints, compatible backends and run history, and shown as code —
the declaration written as Python (generated from what actually runs, so it
also exists for tasks created through the API), the source files of its
pipeline template, and the plan the planner derives from it.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.orm import Session

from api.db import ExecutionModel, TaskModel, get_db
from api.pipelines import REGISTRY, WORKFLOWS_DIR, display_path, pipelines
from api.schemas import (
    GridRow,
    GridRun,
    SourceFile,
    WorkflowDetail,
    WorkflowGrid,
    WorkflowImportError,
    WorkflowRefreshResponse,
    WorkflowRun,
    WorkflowSummary,
)
from api.seed import sync_workflows
from api.services import cognitive_task_of
from ceg.backends import available_backends
from ceg.backends.common import hitl_node_ids
from ceg.codegen import task_to_python
from ceg.models.graph import CEGGraph
from ceg.models.task import CognitiveTask, TaskConstraint
from ceg.planner import PlanningError, plan

router = APIRouter(tags=["Workflows"])


@router.get(
    "/workflows",
    response_model=list[WorkflowSummary],
    summary="Lister les workflows",
)
def list_workflows(db: Session = Depends(get_db)) -> list[WorkflowSummary]:
    """Tous les workflows, avec contraintes, backends compatibles et historique.

    Un fichier ajouté ou modifié dans le dossier des workflows apparaît ici
    sans redémarrer l'API.
    """
    changed = REGISTRY.refresh_if_changed()
    known = {task_id for (task_id,) in db.query(TaskModel.id).all()}
    if changed or set(REGISTRY.workflows) - known:
        sync_workflows(db)
    tasks = db.query(TaskModel).order_by(TaskModel.id).all()
    return [WorkflowSummary(**_summary(db, task)[0]) for task in tasks]


@router.get(
    "/workflows/errors",
    response_model=list[WorkflowImportError],
    summary="Fichiers de workflow en erreur",
)
def workflow_errors() -> list[WorkflowImportError]:
    """Fichiers qui n'ont pas pu être chargés, avec leur erreur."""
    pipelines()
    return [
        WorkflowImportError(file=display_path(path) or path, error=error)
        for path, error in sorted(REGISTRY.errors.items())
    ]


@router.post(
    "/workflows/refresh",
    response_model=WorkflowRefreshResponse,
    summary="Relire le dossier des workflows",
)
def refresh_workflows(db: Session = Depends(get_db)) -> WorkflowRefreshResponse:
    """Recharger tous les fichiers de workflow et synchroniser la base."""
    REGISTRY.load()
    sync_workflows(db)
    return WorkflowRefreshResponse(
        folder=display_path(str(WORKFLOWS_DIR)) or str(WORKFLOWS_DIR),
        workflows=len(REGISTRY.workflows),
        errors=workflow_errors(),
    )


@router.get(
    "/workflows/{id}",
    response_model=WorkflowDetail,
    summary="Code et plan d'un workflow",
)
def get_workflow(id: str, db: Session = Depends(get_db)) -> WorkflowDetail:
    """Le workflow en code : déclaration Python, sources du modèle, plan."""
    task = db.get(TaskModel, id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{id}' non trouvé.",
        )
    fields, declaration, graph = _summary(db, task)
    return WorkflowDetail(
        **fields,
        python_code=task_to_python(declaration),
        declaration=declaration.model_dump(exclude_defaults=True),
        plan=graph.model_dump(exclude={"task"}) if graph else None,
        sources=_sources(task.pipeline),
    )


@router.get(
    "/workflows/{id}/grid",
    response_model=WorkflowGrid,
    summary="Grille exécutions × étapes",
)
def workflow_grid(
    id: str,
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
) -> WorkflowGrid:
    """Statut de chaque étape dans les dernières exécutions (vue Grille)."""
    task = db.get(TaskModel, id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{id}' non trouvé.",
        )
    runs = (
        db.query(ExecutionModel)
        .filter(ExecutionModel.task_id == id)
        .order_by(ExecutionModel.started_at.desc())
        .limit(limit)
        .all()
    )[::-1]

    node_ids: list[str] = []
    try:
        node_ids = [n.id for n in plan(cognitive_task_of(task)).nodes]
    except PlanningError:
        pass
    statuses_by_run = [_step_statuses(run) for run in runs]
    for statuses in statuses_by_run:
        node_ids += [n for n in statuses if n not in node_ids]

    return WorkflowGrid(
        runs=[
            GridRun(
                id=run.id,
                status=run.status,
                started_at=run.started_at.isoformat() if run.started_at else None,
                backend=run.backend,
                optimizer=run.optimizer,
                total_cost=run.total_cost,
                total_latency_ms=run.total_latency_ms,
            )
            for run in runs
        ],
        rows=[
            GridRow(
                node_id=node_id,
                statuses=[statuses.get(node_id) for statuses in statuses_by_run],
            )
            for node_id in node_ids
        ],
    )


def _step_statuses(run: ExecutionModel) -> dict[str, str]:
    """Final status of each top-level step of a run (from its trace)."""
    state = json.loads(run.workflow_state_json or "{}")
    statuses: dict[str, str] = {}
    for entry in state.get("execution_log", []):
        if not entry.get("subgraph_parent"):
            statuses[entry["node_id"]] = entry.get("status", "completed")
    if run.status == "awaiting_approval":
        for pending in state.get("pending_approvals", []):
            statuses.setdefault(pending.get("node_id", ""), "awaiting_approval")
    return statuses


def _summary(
    db: Session, task: TaskModel
) -> tuple[dict[str, Any], CognitiveTask, CEGGraph | None]:
    """Summary fields, the effective declaration and its plan (if valid)."""
    declaration = cognitive_task_of(task)
    graph: CEGGraph | None
    try:
        graph = plan(declaration)
        error = None
    except PlanningError as exc:
        graph, error = None, str(exc)

    runs = (
        db.query(ExecutionModel)
        .filter(ExecutionModel.task_id == task.id)
        .order_by(ExecutionModel.started_at.desc())
        .all()
    )
    finished = [r for r in runs if r.status in ("completed", "failed")]
    last = runs[0] if runs else None

    fields: dict[str, Any] = {
        "id": task.id,
        "name": task.name,
        "objective": task.objective,
        "pipeline": task.pipeline,
        "source_file": _source_file(task.pipeline),
        "steps": len(declaration.subtasks),
        "constraints": declaration.task_constraints or TaskConstraint(),
        "tools_allowed": declaration.tools_allowed,
        "requires_approval": bool(graph and hitl_node_ids(graph)),
        "backends": _compatible_backends(graph),
        "valid": graph is not None,
        "error": error,
        "runs": len(runs),
        "success_rate": (
            round(sum(r.status == "completed" for r in finished) / len(finished), 3)
            if finished
            else None
        ),
        "last_run": (
            WorkflowRun(
                id=last.id,
                status=last.status,
                started_at=last.started_at.isoformat() if last.started_at else None,
            )
            if last
            else None
        ),
    }
    return fields, declaration, graph


def _compatible_backends(graph: CEGGraph | None) -> list[str]:
    """Backends that accept the plan as declared (compilation succeeds)."""
    if graph is None:
        return []
    names = []
    for backend in available_backends():
        try:
            backend.compile(
                graph,
                checkpointer=MemorySaver() if backend.supports_hitl else None,
            )
        except ValueError:
            continue
        names.append(backend.name)
    return names


def _source_file(pipeline: str | None) -> str | None:
    definition = pipelines().get(pipeline) if pipeline else None
    return display_path(definition.source_file) if definition else None


def _sources(pipeline: str | None) -> list[SourceFile]:
    """The workflow's declaration and executor, as they are in its files."""
    definition = pipelines().get(pipeline) if pipeline else None
    if definition is None:
        return []
    sources = []
    for title, obj in (
        ("Déclaration", definition.declare),
        ("Exécuteur (implémentation des étapes)", definition.executor_class),
    ):
        try:
            lines, start = inspect.getsourcelines(obj)
            path = inspect.getsourcefile(obj)
        except (OSError, TypeError):
            continue
        sources.append(
            SourceFile(
                title=title,
                path=display_path(path) or "",
                start_line=start,
                code="".join(lines),
            )
        )
    return sources
