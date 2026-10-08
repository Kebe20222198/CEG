"""Demonstration pipelines for Advanced Control Flow mechanisms in CEG.

Each pipeline is a CognitiveTask declaration; its graph comes from the
planner. Provides:
  1. analyse_ventes_parallele: independent sub-tasks → parallel fan-out / fan-in
  2. redaction_rapport_iteratif: ``repeat`` → bounded critique/revision loop
  3. validation_budget_hitl: ``requires_approval`` → human-in-the-loop
"""

from __future__ import annotations

from typing import Any

from ceg.compiler.mock_executor import ExecutionError, ExecutionResult, MockExecutor
from ceg.models.graph import CEGGraph
from ceg.models.task import CognitiveTask, RepeatSpec, SubTask, TaskConstraint
from ceg.planner import plan
from ceg.registry import workflow

# ── 1. Parallel Pipeline (Fan-out / Fan-in) ──────────────────────────────────


@workflow(executor=lambda: ParallelSalesExecutor())
def analyse_ventes_parallele() -> CognitiveTask:
    """Déclaration : analyse multi-régions avec alerte conditionnelle.

    Les trois extractions ne dépendent que de ``init`` : le planificateur
    les exécute en parallèle sans qu'on le lui demande.
    """
    return CognitiveTask(
        name="analyse_ventes_parallele",
        objective=(
            "Extraire en parallèle les données Nord, Sud et Est puis fusionner "
            "dans un nœud de jointure."
        ),
        task_constraints=TaskConstraint(),
        subtasks=[
            SubTask(
                id="init",
                objective="Initialiser les paramètres de configuration multi-régions",
                model_tier_hint="fast",
            ),
            *[
                SubTask(
                    id=f"fetch_{region.lower()}",
                    objective=f"Extraire les transactions de la région {region}",
                    dependencies=["init"],
                    required_capabilities=["data_retrieval"],
                    model_tier_hint="fast",
                )
                for region in ("Nord", "Sud", "Est")
            ],
            SubTask(
                id="aggregate_multi",
                objective="Fusionner et agréger les données des 3 régions en parallèle",
                dependencies=["fetch_nord", "fetch_sud", "fetch_est"],
                required_capabilities=["data_analysis"],
                model_tier_hint="balanced",
            ),
            SubTask(
                id="detect_anomaly",
                objective="Détecter les chutes de volume régionales (> 20%)",
                dependencies=["aggregate_multi"],
                required_capabilities=["anomaly_detection"],
                model_tier_hint="quality",
            ),
            SubTask(
                id="generate_alert",
                objective="Générer le rapport d'alerte exécutif",
                run_if="detect_anomaly.anomalies_found",
                required_capabilities=["text_generation"],
                model_tier_hint="balanced",
            ),
        ],
    )


def build_parallel_sales_graph() -> CEGGraph:
    """Plan the parallel multi-region sales analysis pipeline.

         ┌──→ fetch_nord ──┐
    init ┼──→ fetch_sud  ──┼──→ aggregate_multi → detect_anomaly → [COND] generate_alert
         └──→ fetch_est  ──┘
    """
    return plan(analyse_ventes_parallele())


class ParallelSalesExecutor(MockExecutor):
    """Executor for parallel regional sales pipeline."""

    def run(
        self,
        node_id: str,
        objective: str,
        inputs: dict[str, Any],
        attempt: int = 1,
    ) -> ExecutionResult:
        if node_id == "init":
            return ExecutionResult(
                output={"period": "2024-Q1", "regions": ["Nord", "Sud", "Est"]},
                cost=0.0005,
                latency_ms=25.0,
                confidence=1.0,
            )
        elif node_id == "fetch_nord":
            return ExecutionResult(
                output={"region": "Nord", "total_ventes": 37000.0, "transactions": 10},
                cost=0.001,
                latency_ms=45.0,
                confidence=0.99,
            )
        elif node_id == "fetch_sud":
            return ExecutionResult(
                output={"region": "Sud", "total_ventes": 44000.0, "transactions": 10},
                cost=0.001,
                latency_ms=40.0,
                confidence=0.99,
            )
        elif node_id == "fetch_est":
            return ExecutionResult(
                output={"region": "Est", "total_ventes": 40000.0, "transactions": 10},
                cost=0.001,
                latency_ms=50.0,
                confidence=0.99,
            )
        elif node_id == "aggregate_multi":
            return ExecutionResult(
                output={
                    "total_global": 121000.0,
                    "regions": {
                        "Nord": {"m1": 50000.0, "m2": 37000.0, "var": -26.0},
                        "Sud": {"m1": 45000.0, "m2": 44000.0, "var": -2.2},
                        "Est": {"m1": 38000.0, "m2": 40000.0, "var": 5.2},
                    },
                },
                cost=0.002,
                latency_ms=60.0,
                confidence=0.98,
            )
        elif node_id == "detect_anomaly":
            return ExecutionResult(
                output={
                    "anomalies": [{"region": "Nord", "var": -26.0}],
                    "anomalies_found": True,
                },
                cost=0.003,
                latency_ms=80.0,
                confidence=0.97,
            )
        elif node_id == "generate_alert":
            return ExecutionResult(
                output={
                    "alert_message": (
                        "ALERTE: Baisse de 26% détectée dans la région Nord."
                    ),
                    "alert_sent": True,
                },
                cost=0.004,
                latency_ms=100.0,
                confidence=0.96,
            )
        raise ExecutionError(node_id=node_id, reason=f"Nœud inconnu: {node_id}")


# ── 2. Iterative Refinement Loop Pipeline (EdgeType.LOOP) ────────────────────


@workflow(executor=lambda: LoopReportExecutor())
def redaction_rapport_iteratif() -> CognitiveTask:
    """Déclaration : rapport rédigé puis critiqué, révisé au plus 3 fois."""
    return CognitiveTask(
        name="redaction_rapport_iteratif",
        objective=(
            "Rédiger un rapport stratégique avec cycle d'auto-critique et "
            "raffinement (max 3 boucles)."
        ),
        task_constraints=TaskConstraint(),
        subtasks=[
            SubTask(
                id="rediger_brouillon",
                objective="Rédiger ou réviser la synthèse stratégique trimestrielle",
                model_tier_hint="quality",
            ),
            SubTask(
                id="evaluer_critique",
                objective="Évaluer la rigueur, le style et la conformité du rapport",
                dependencies=["rediger_brouillon"],
                required_capabilities=["evaluation", "reasoning"],
                model_tier_hint="quality",
                repeat=RepeatSpec(
                    back_to="rediger_brouillon",
                    while_key="needs_revision",
                    max_iterations=3,
                ),
            ),
            SubTask(
                id="publier_rapport",
                objective="Formater et exporter le rapport validé en version finale",
                dependencies=["evaluer_critique"],
                model_tier_hint="fast",
            ),
        ],
    )


def build_loop_report_graph() -> CEGGraph:
    """Plan the iterative report drafting pipeline with self-correction loop.

    rediger_brouillon ──→ evaluer_critique ──[LOOP max 3]──┐
         ↑                                                 │
         └─────────────────────────────────────────────────┘
                               │
                               └──[SEQUENTIAL]──→ publier_rapport
    """
    return plan(redaction_rapport_iteratif())


class LoopReportExecutor(MockExecutor):
    """Executor for iterative drafting and critique loop."""

    def __init__(self) -> None:
        super().__init__()
        self._iteration = 0

    def run(
        self,
        node_id: str,
        objective: str,
        inputs: dict[str, Any],
        attempt: int = 1,
    ) -> ExecutionResult:
        if node_id == "rediger_brouillon":
            self._iteration += 1
            score = 0.65 + (self._iteration * 0.12)
            return ExecutionResult(
                output={
                    "iteration": self._iteration,
                    "draft_content": (
                        f"Version {self._iteration} du rapport stratégique "
                        "trimestriel (Q1 2024)."
                    ),
                    "word_count": 450 + (self._iteration * 50),
                },
                cost=0.005,
                latency_ms=120.0,
                confidence=min(0.99, score),
            )
        elif node_id == "evaluer_critique":
            is_valid = self._iteration >= 2
            return ExecutionResult(
                output={
                    "quality_score": 0.72 if self._iteration == 1 else 0.94,
                    "needs_revision": not is_valid,
                    "is_approved": is_valid,
                    "feedback": "Points d'action clarifiés et cohérence validée."
                    if is_valid
                    else "Manque d'exemples chiffrés sur le churn.",
                },
                cost=0.004,
                latency_ms=90.0,
                confidence=0.95,
            )
        elif node_id == "publier_rapport":
            return ExecutionResult(
                output={
                    "published_url": "https://ceg.internal/reports/q1-2024-final.pdf",
                    "status": "published",
                    "total_revisions": self._iteration,
                },
                cost=0.001,
                latency_ms=30.0,
                confidence=1.0,
            )
        raise ExecutionError(node_id=node_id, reason=f"Nœud inconnu: {node_id}")


# ── 3. Human-in-the-Loop Pipeline (HITL) ─────────────────────────────────────


@workflow(executor=lambda: HITLBudgetExecutor())
def validation_budget_hitl() -> CognitiveTask:
    """Déclaration : aucun décaissement sans validation humaine."""
    return CognitiveTask(
        name="validation_budget_hitl",
        objective=(
            "Calculer les budgets alloués avec point d'arrêt obligatoire pour "
            "validation humaine."
        ),
        task_constraints=TaskConstraint(),
        subtasks=[
            SubTask(
                id="calculer_budget",
                objective="Calculer les montants alloués par département",
                model_tier_hint="fast",
            ),
            SubTask(
                id="validation_manager",
                objective="Validation humaine obligatoire avant décaissement (HITL)",
                dependencies=["calculer_budget"],
                requires_approval=True,
                model_tier_hint="balanced",
            ),
            SubTask(
                id="decaisser_fonds",
                objective="Exécuter les virements bancaires départementaux",
                dependencies=["validation_manager"],
                model_tier_hint="fast",
            ),
        ],
    )


def build_hitl_budget_graph() -> CEGGraph:
    """Plan the budget allocation pipeline with human approval checkpoint.

    calculer_budget ──→ validation_manager [HITL Interruption] ──→ decaisser_fonds
    """
    return plan(validation_budget_hitl())


class HITLBudgetExecutor(MockExecutor):
    """Executor for human-in-the-loop budget validation pipeline."""

    def run(
        self,
        node_id: str,
        objective: str,
        inputs: dict[str, Any],
        attempt: int = 1,
    ) -> ExecutionResult:
        if node_id == "calculer_budget":
            return ExecutionResult(
                output={
                    "montant_demande": 45000.0,
                    "departement": "Intelligence Artificielle",
                    "justification": "Souscription GPU Cloud cluster H100",
                },
                cost=0.001,
                latency_ms=35.0,
                confidence=1.0,
            )
        elif node_id == "validation_manager":
            return ExecutionResult(
                output={
                    "decision": "APPROVED",
                    "approbateur": "Directeur Financier",
                    "timestamp": "2026-08-25T11:00:00Z",
                },
                cost=0.000,
                latency_ms=10.0,
                confidence=1.0,
            )
        elif node_id == "decaisser_fonds":
            return ExecutionResult(
                output={
                    "statut_virement": "EXECUTE",
                    "reference_bancaire": "VIR-2024-8849-EUR",
                    "montant": 45000.0,
                },
                cost=0.001,
                latency_ms=20.0,
                confidence=1.0,
            )
        raise ExecutionError(node_id=node_id, reason=f"Nœud inconnu: {node_id}")
