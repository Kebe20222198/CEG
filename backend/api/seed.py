"""Seed the SQLite DB with demo tasks and one execution per control-flow type.

1. analyse_ventes_alertes: Sequential + Conditional edges
2. analyse_ventes_parallele: EdgeType.PARALLEL (fan-out / fan-in multi-region fetching)
3. redaction_rapport_iteratif: EdgeType.LOOP (iterative critique and self-correction)
4. multi_agent_supervisor: nested subgraphs
5. validation_budget_hitl: Human-in-the-Loop (pauses, awaiting approval)

Run from ``backend/``:  ``python -m api.seed``  (``--reset`` first deletes the
existing executions, traces and metrics).
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy.orm import Session

from api.db import (
    ExecutionModel,
    MetricModel,
    SessionLocal,
    TaskModel,
    TraceModel,
    init_db,
)
from api.schemas import ExecuteTaskRequest
from api.services import ExecutionRequestError, start_execution

TASKS_DEF: list[dict[str, Any]] = [
    {
        "id": "analyse_ventes_alertes",
        "name": "Pipeline Ventes (Séquentiel + Conditionnel)",
        "objective": (
            "Agréger les ventes régionales et alerter si chute de volume > 20%."
        ),
        "tools": ["csv_reader", "zscore_detector", "slack_alert"],
        "subtasks": [
            {"id": "fetch", "objective": "Extraction CSV"},
            {"id": "detect", "objective": "Détection anomalies"},
            {"id": "alert", "objective": "Génération alerte"},
        ],
        "demo": ExecuteTaskRequest(scenario_name="scenario_b_single_anomaly"),
    },
    {
        "id": "analyse_ventes_parallele",
        "name": "Pipeline Multi-Régions Parallèle (Fan-out / Fan-in)",
        "objective": (
            "Extraire en parallèle les données Nord, Sud et Est puis fusionner "
            "dans un nœud de jointure."
        ),
        "tools": ["parallel_retriever", "matrix_aggregator", "anomaly_detector"],
        "subtasks": [
            {"id": "init", "objective": "Paramétrage initial"},
            {"id": "fetch_regions", "objective": "Extractions régionales concurrentes"},
            {"id": "aggregate_multi", "objective": "Consolidation et jointure"},
        ],
        "demo": ExecuteTaskRequest(
            scenario_name="multi_region_q1_parallel", inputs={"period": "2024-Q1"}
        ),
    },
    {
        "id": "redaction_rapport_iteratif",
        "name": "Rédaction & Critique Itérative (EdgeType.LOOP)",
        "objective": (
            "Rédiger un rapport stratégique avec cycle d'auto-critique et "
            "raffinement (max 3 boucles)."
        ),
        "tools": ["llm_drafter", "critique_evaluator", "pdf_exporter"],
        "subtasks": [
            {"id": "draft", "objective": "Rédaction du brouillon"},
            {"id": "critique", "objective": "Évaluation et notation qualité"},
            {"id": "publish", "objective": "Publication finale"},
        ],
        "demo": ExecuteTaskRequest(
            scenario_name="rapport_strategique_iteratif",
            inputs={"topic": "Q1 Performance"},
        ),
    },
    {
        "id": "multi_agent_supervisor",
        "name": "Superviseur Multi-Agents Hiérarchique (Sous-graphes)",
        "objective": (
            "Superviser des équipes spécialisées autonomes (Recherche & Analyse) "
            "encapsulées dans des sous-graphes."
        ),
        "tools": [
            "supervisor_agent",
            "research_subgraph",
            "analytics_subgraph",
            "executive_synthesizer",
        ],
        "subtasks": [
            {"id": "supervise", "objective": "Délégation et planification"},
            {"id": "research_team", "objective": "Sous-graphe équipe recherche"},
            {"id": "analytics_team", "objective": "Sous-graphe équipe analyse"},
            {"id": "executive", "objective": "Synthèse exécutive"},
        ],
        "demo": ExecuteTaskRequest(
            scenario_name="supervision_strategique_ia",
            inputs={"query": "Expansion Marché Entreprise"},
        ),
    },
    {
        "id": "validation_budget_hitl",
        "name": "Validation Budgétaire (Human-in-the-Loop)",
        "objective": (
            "Calculer les budgets alloués avec point d'arrêt obligatoire pour "
            "validation humaine."
        ),
        "tools": ["budget_calculator", "hitl_checkpoint", "payment_gateway"],
        "subtasks": [
            {"id": "calculate", "objective": "Calcul budgétaire"},
            {"id": "validate", "objective": "Signature managériale"},
            {"id": "disburse", "objective": "Virement bancaire"},
        ],
        "demo": ExecuteTaskRequest(
            scenario_name="demande_gpu_cloud_hitl", inputs={"amount": 45000}
        ),
    },
]


def _ensure_tasks(db: Session) -> None:
    """Create the demo tasks; link existing ones to their pipeline template."""
    for t_def in TASKS_DEF:
        task = db.get(TaskModel, t_def["id"])
        if task is None:
            db.add(
                TaskModel(
                    id=t_def["id"],
                    name=t_def["name"],
                    objective=t_def["objective"],
                    pipeline=t_def["id"],
                    task_constraints_json=None,
                    tools_allowed_json=json.dumps(t_def["tools"]),
                    subtasks_json=json.dumps(t_def["subtasks"]),
                    evaluation_criteria_json=json.dumps([]),
                )
            )
            print(f"✓ Task '{t_def['id']}' créée en base.")
        elif task.pipeline is None:
            # Tasks created before the pipeline column existed.
            task.pipeline = t_def["id"]
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
        _ensure_tasks(db)
        if reset:
            _reset_executions(db)

        if db.query(ExecutionModel).count() and not (force or reset):
            print("✓ Base déjà ensemencée.")
            return

        print("— Ensemencement des exécutions de démonstration —")
        for t_def in TASKS_DEF:
            task = db.get(TaskModel, t_def["id"])
            assert task is not None
            try:
                record = start_execution(db, task, t_def["demo"])
            except ExecutionRequestError as exc:
                print(f"  ✗ {t_def['id']}: {exc}")
                continue
            print(f"  ✓ {t_def['id']}: id={record.id}, status={record.status}")
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
