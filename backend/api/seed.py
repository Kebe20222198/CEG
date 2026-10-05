"""Seed script to populate SQLite DB with real executions from all 4 control flow mechanisms.

1. analyse_ventes_alertes: Sequential + Conditional edges
2. analyse_ventes_parallele: EdgeType.PARALLEL (fan-out / fan-in multi-region fetching)
3. redaction_rapport_iteratif: EdgeType.LOOP (iterative critique and self-correction)
4. validation_budget_hitl: Human-in-the-Loop (interrupt_before approval checkpoint)
"""

import json
from pathlib import Path
import sys

# Ensure root directory is in sys.path
ROOT_DIR = Path(__file__).parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from api.db import SessionLocal, init_db, TaskModel, ExecutionModel
from api.routers.tasks import execute_task
from api.schemas import ExecuteTaskRequest
from ceg.use_cases.sales_pipeline import analyse_ventes_alertes

TASKS_DEF = [
    {
        "id": "analyse_ventes_alertes",
        "name": "Pipeline Ventes (Séquentiel + Conditionnel)",
        "objective": "Agréger les ventes régionales et alerter si chute de volume > 20%.",
        "tools": ["csv_reader", "zscore_detector", "slack_alert"],
        "subtasks": [
            {"id": "fetch", "objective": "Extraction CSV"},
            {"id": "detect", "objective": "Détection anomalies"},
            {"id": "alert", "objective": "Génération alerte"},
        ],
    },
    {
        "id": "analyse_ventes_parallele",
        "name": "Pipeline Multi-Régions Parallèle (Fan-out / Fan-in)",
        "objective": "Extraire en parallèle les données Nord, Sud et Est puis fusionner dans un nœud de jointure.",
        "tools": ["parallel_retriever", "matrix_aggregator", "anomaly_detector"],
        "subtasks": [
            {"id": "init", "objective": "Paramétrage initial"},
            {"id": "fetch_regions", "objective": "Extractions régionales concurrentes"},
            {"id": "aggregate_multi", "objective": "Consolidation et jointure"},
        ],
    },
    {
        "id": "redaction_rapport_iteratif",
        "name": "Rédaction & Critique Itérative (EdgeType.LOOP)",
        "objective": "Rédiger un rapport stratégique avec cycle d'auto-critique et raffinement (max 3 boucles).",
        "tools": ["llm_drafter", "critique_evaluator", "pdf_exporter"],
        "subtasks": [
            {"id": "draft", "objective": "Rédaction du brouillon"},
            {"id": "critique", "objective": "Évaluation et notation qualité"},
            {"id": "publish", "objective": "Publication finale"},
        ],
    },
    {
        "id": "multi_agent_supervisor",
        "name": "Superviseur Multi-Agents Hiérarchique (Sous-graphes)",
        "objective": "Superviser des équipes spécialisées autonomes (Recherche & Analyse) encapsulées dans des sous-graphes.",
        "tools": ["supervisor_agent", "research_subgraph", "analytics_subgraph", "executive_synthesizer"],
        "subtasks": [
            {"id": "supervise", "objective": "Délégation et planification"},
            {"id": "research_team", "objective": "Sous-graphe équipe recherche"},
            {"id": "analytics_team", "objective": "Sous-graphe équipe analyse"},
            {"id": "executive", "objective": "Synthèse exécutive"},
        ],
    },
    {
        "id": "validation_budget_hitl",
        "name": "Validation Budgétaire (Human-in-the-Loop)",
        "objective": "Calculer les budgets alloués avec point d'arrêt obligatoire pour validation humaine.",
        "tools": ["budget_calculator", "hitl_checkpoint", "payment_gateway"],
        "subtasks": [
            {"id": "calculate", "objective": "Calcul budgétaire"},
            {"id": "validate", "objective": "Signature managériale"},
            {"id": "disburse", "objective": "Virement bancaire"},
        ],
    },
]


def seed_database(force: bool = False) -> None:
    """Initialize DB and seed tasks + representative executions for each flow type."""
    init_db()
    db = SessionLocal()

    try:
        # 1. Register all 4 tasks
        for t_def in TASKS_DEF:
            existing_task = db.query(TaskModel).filter(TaskModel.id == t_def["id"]).first()
            if not existing_task:
                task_model = TaskModel(
                    id=t_def["id"],
                    name=t_def["name"],
                    objective=t_def["objective"],
                    task_constraints_json=None,
                    tools_allowed_json=json.dumps(t_def["tools"]),
                    subtasks_json=json.dumps(t_def["subtasks"]),
                    evaluation_criteria_json=json.dumps([]),
                )
                db.add(task_model)
                db.commit()
                print(f"✓ Task '{t_def['id']}' créée en base.")

        # 2. Check if we need to seed executions for the new control flow tasks
        existing_exec_count = db.query(ExecutionModel).count()
        has_parallel = db.query(ExecutionModel).filter(ExecutionModel.task_id == "analyse_ventes_parallele").first()

        if not has_parallel or force:
            print("— Ensemencement des exécutions des 4 flux de contrôle (Sequential, Parallel, Loop, HITL) —")

            # Sequential / Conditional
            try:
                csv_path = str(ROOT_DIR / "tests/fixtures/scenario_b_single_anomaly.csv")
                req = ExecuteTaskRequest(scenario_name="scenario_b_single_anomaly", csv_path=csv_path)
                res = execute_task(id="analyse_ventes_alertes", payload=req, db=db)
                print(f"  ✓ Séquentiel/Conditionnel: id={res.id}, status={res.status}")
            except Exception as e:
                print(f"  ✗ Séquentiel error: {e}")

            # Parallel Fan-out / Fan-in
            try:
                req = ExecuteTaskRequest(scenario_name="multi_region_q1_parallel", inputs={"period": "2024-Q1"})
                res = execute_task(id="analyse_ventes_parallele", payload=req, db=db)
                print(f"  ✓ Parallèle (Fan-out/in): id={res.id}, status={res.status}")
            except Exception as e:
                print(f"  ✗ Parallèle error: {e}")

            # Iterative Loop
            try:
                req = ExecuteTaskRequest(scenario_name="rapport_strategique_iteratif", inputs={"topic": "Q1 Performance"})
                res = execute_task(id="redaction_rapport_iteratif", payload=req, db=db)
                print(f"  ✓ Boucle Itérative (Loop): id={res.id}, status={res.status}")
            except Exception as e:
                print(f"  ✗ Loop error: {e}")

            # Hierarchical Multi-Agent Supervisor with Subgraphs
            try:
                req = ExecuteTaskRequest(scenario_name="supervision_strategique_ia", inputs={"query": "Expansion Marché Entreprise"})
                res = execute_task(id="multi_agent_supervisor", payload=req, db=db)
                print(f"  ✓ Superviseur Multi-Agents (Sous-graphes): id={res.id}, status={res.status}")
            except Exception as e:
                print(f"  ✗ Superviseur error: {e}")

            # HITL
            try:
                req = ExecuteTaskRequest(scenario_name="demande_gpu_cloud_hitl", inputs={"amount": 45000})
                res = execute_task(id="validation_budget_hitl", payload=req, db=db)
                print(f"  ✓ Validation HITL: id={res.id}, status={res.status}")
            except Exception as e:
                print(f"  ✗ HITL error: {e}")

            print("✓ Ensemencement terminé avec succès.")
        else:
            print(f"✓ Base déjà ensemencée ({existing_exec_count} exécutions trouvées).")

    finally:
        db.close()


if __name__ == "__main__":
    seed_database(force=True)
