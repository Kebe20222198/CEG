"""Seed the SQLite DB with demo tasks and one execution per control-flow type.

Each demo task is stored from its CognitiveTask declaration (see
``api.pipelines``), so what the database holds is what gets planned:

1. analyse_ventes_alertes: sequential steps + ``run_if`` condition
2. analyse_ventes_parallele: independent sub-tasks → parallel fan-out / fan-in
3. redaction_rapport_iteratif: ``repeat`` → bounded critique loop
4. multi_agent_supervisor: nested sub-tasks → sub-graphs
5. validation_budget_hitl: ``requires_approval`` → pauses, awaiting approval

Run from ``backend/``:  ``python -m api.seed``  (``--reset`` first deletes the
existing executions, traces and metrics).
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy.orm import Session

from api.db import (
    ExecutionModel,
    MetricModel,
    SessionLocal,
    TaskModel,
    TraceModel,
    init_db,
)
from api.pipelines import pipelines
from api.schemas import ExecuteTaskRequest
from api.services import ExecutionRequestError, start_execution

# (task id = pipeline id, display name, demo execution request)
DEMOS: list[tuple[str, str, ExecuteTaskRequest]] = [
    (
        "analyse_ventes_alertes",
        "Pipeline Ventes (Séquentiel + Conditionnel)",
        ExecuteTaskRequest(scenario_name="scenario_b_single_anomaly"),
    ),
    (
        "analyse_ventes_parallele",
        "Pipeline Multi-Régions Parallèle (Fan-out / Fan-in)",
        ExecuteTaskRequest(
            scenario_name="multi_region_q1_parallel", inputs={"period": "2024-Q1"}
        ),
    ),
    (
        "redaction_rapport_iteratif",
        "Rédaction & Critique Itérative (Boucle)",
        ExecuteTaskRequest(
            scenario_name="rapport_strategique_iteratif",
            inputs={"topic": "Q1 Performance"},
        ),
    ),
    (
        "multi_agent_supervisor",
        "Superviseur Multi-Agents Hiérarchique (Sous-graphes)",
        ExecuteTaskRequest(
            scenario_name="supervision_strategique_ia",
            inputs={"query": "Expansion Marché Entreprise"},
        ),
    ),
    (
        "validation_budget_hitl",
        "Validation Budgétaire (Human-in-the-Loop)",
        ExecuteTaskRequest(
            scenario_name="demande_gpu_cloud_hitl", inputs={"amount": 45000}
        ),
    ),
]


def sync_workflows(db: Session) -> None:
    """Create or refresh a task for every discovered workflow.

    Like Airflow syncing its DAG files to its database: the workflow file is
    the source of truth, so the stored declaration is refreshed from it.
    """
    names = {task_id: name for task_id, name, _ in DEMOS}
    for workflow_id, definition in pipelines().items():
        declaration = definition.declare()
        task = db.get(TaskModel, workflow_id)
        if task is None:
            task = TaskModel(
                id=workflow_id,
                name=names.get(workflow_id) or definition.description or workflow_id,
            )
            db.add(task)
            print(f"✓ Workflow '{workflow_id}' enregistré.")
        task.pipeline = workflow_id
        task.objective = declaration.objective
        task.task_constraints_json = (
            declaration.task_constraints.model_dump_json()
            if declaration.task_constraints
            else None
        )
        task.tools_allowed_json = json.dumps(declaration.tools_allowed)
        task.subtasks_json = json.dumps(
            [st.model_dump() for st in declaration.subtasks]
        )
        task.evaluation_criteria_json = task.evaluation_criteria_json or "[]"
    db.commit()


def _reset_executions(db: Session) -> None:
    for model in (TraceModel, MetricModel, ExecutionModel):
        db.query(model).delete()
    db.commit()
    print("✓ Exécutions, traces et métriques supprimées.")


def seed_database(force: bool = False, reset: bool = False) -> None:
    """Initialize DB and seed tasks + one demo execution per flow type.

    Args:
        force: Run the demo executions even if some already exist.
        reset: Delete every execution, trace and metric first (implies force).
    """
    init_db()
    db = SessionLocal()
    try:
        sync_workflows(db)
        if reset:
            _reset_executions(db)

        if db.query(ExecutionModel).count() and not (force or reset):
            print("✓ Base déjà ensemencée.")
            return

        print("— Ensemencement des exécutions de démonstration —")
        for task_id, _, request in DEMOS:
            task = db.get(TaskModel, task_id)
            assert task is not None
            try:
                record = start_execution(db, task, request)
            except ExecutionRequestError as exc:
                print(f"  ✗ {task_id}: {exc}")
                continue
            print(f"  ✓ {task_id}: id={record.id}, status={record.status}")
        print("✓ Ensemencement terminé.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reset",
        action="store_true",
        help="supprimer les exécutions existantes avant d'ensemencer",
    )
    args = parser.parse_args()
    seed_database(force=True, reset=args.reset)
