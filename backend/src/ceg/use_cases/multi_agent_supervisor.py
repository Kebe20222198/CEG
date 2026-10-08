"""Hierarchical Multi-Agent Supervisor Pattern with Subgraphs in CEG.

Demonstrates:
  1. Nested Subgraphs (composition inside CEGNode).
  2. Supervisor agent orchestrating specialized team subgraphs.
  3. Team 1: Research Team (Parallel web & database retrieval).
  4. Team 2: Analytics & Quality Team (Iterative critique and evaluation).
"""

from __future__ import annotations

from typing import Any

from ceg.compiler.mock_executor import ExecutionError, ExecutionResult, MockExecutor
from ceg.models.graph import CEGGraph
from ceg.models.task import CognitiveTask, RepeatSpec, SubTask, TaskConstraint
from ceg.planner import plan, plan_subtasks
from ceg.registry import workflow

# ── Team 1: Research Team ────────────────────────────────────────────────────


def research_team_subtasks() -> list[SubTask]:
    """Research team: web and database sources searched in parallel.

         ┌──→ web_search_agent ──┐
    init ┤                       ├──→ merge_research
         └──→ db_extractor_agent ┘
    """
    return [
        SubTask(
            id="research_init",
            objective="Initialiser les paramètres de recherche contextuelle",
            model_tier_hint="fast",
        ),
        SubTask(
            id="web_search_agent",
            objective="Recherche documentaire web et veille concurrentielle",
            dependencies=["research_init"],
            required_capabilities=["web_search", "information_retrieval"],
            model_tier_hint="fast",
        ),
        SubTask(
            id="db_extractor_agent",
            objective="Extraction des métriques internes et historique SQL",
            dependencies=["research_init"],
            required_capabilities=["data_retrieval"],
            model_tier_hint="fast",
        ),
        SubTask(
            id="merge_research",
            objective="Consolider et dédupliquer les sources de recherche",
            dependencies=["web_search_agent", "db_extractor_agent"],
            required_capabilities=["data_analysis"],
            model_tier_hint="balanced",
        ),
    ]


# ── Team 2: Analytics & Quality Team ─────────────────────────────────────────


def analytics_team_subtasks() -> list[SubTask]:
    """Analytics team: analysis audited and recomputed at most twice.

    model_analysis ──→ quality_audit ──[LOOP max 2]──┐
          ↑                                          │
          └──────────────────────────────────────────┘
                              │
                              └──→ formatted_insights
    """
    return [
        SubTask(
            id="model_analysis",
            objective="Calculer les projections financières et analyses d'impact",
            model_tier_hint="quality",
        ),
        SubTask(
            id="quality_audit",
            objective="Audit de cohérence et vérification des hypothèses",
            dependencies=["model_analysis"],
            required_capabilities=["reasoning", "evaluation"],
            model_tier_hint="quality",
            repeat=RepeatSpec(
                back_to="model_analysis",
                while_key="needs_recalculation",
                max_iterations=2,
            ),
        ),
        SubTask(
            id="formatted_insights",
            objective="Mise en forme des recommandations actionnables",
            dependencies=["quality_audit"],
            model_tier_hint="fast",
        ),
    ]


def build_research_team_subgraph() -> CEGGraph:
    """Plan the research team on its own (sub-graph of the supervisor)."""
    return plan_subtasks(research_team_subtasks(), tools_allowed=[])


def build_analytics_team_subgraph() -> CEGGraph:
    """Plan the analytics team on its own (sub-graph of the supervisor)."""
    return plan_subtasks(analytics_team_subtasks(), tools_allowed=[])


# ── Top-Level Hierarchical Multi-Agent Task ──────────────────────────────────


@workflow(
    id="multi_agent_supervisor", executor=lambda: HierarchicalSupervisorExecutor()
)
def supervision_strategique() -> CognitiveTask:
    """Déclaration : un superviseur délègue à deux équipes autonomes.

    Chaque équipe est une sous-tâche dont les propres sous-tâches deviennent
    un sous-graphe.
    """
    return CognitiveTask(
        name="multi_agent_supervisor",
        objective=(
            "Superviser des équipes spécialisées autonomes (Recherche & Analyse) "
            "encapsulées dans des sous-graphes."
        ),
        task_constraints=TaskConstraint(),
        subtasks=[
            SubTask(
                id="supervisor_dispatch",
                objective=(
                    "Superviser l'analyse globale et déléguer aux équipes spécialisées"
                ),
                model_tier_hint="quality",
            ),
            SubTask(
                id="research_team",
                objective="Équipe Recherche : Collecte multi-sources et veille",
                dependencies=["supervisor_dispatch"],
                model_tier_hint="balanced",
                subtasks=research_team_subtasks(),
            ),
            SubTask(
                id="analytics_team",
                objective="Équipe Analyse & Risques : Modélisation et audit itératif",
                dependencies=["research_team"],
                model_tier_hint="quality",
                subtasks=analytics_team_subtasks(),
            ),
            SubTask(
                id="executive_summary",
                objective=(
                    "Consolider la synthèse décisionnelle finale pour la direction"
                ),
                dependencies=["analytics_team"],
                model_tier_hint="quality",
            ),
        ],
    )


def build_hierarchical_supervisor_graph() -> CEGGraph:
    """Plan the supervisor with its two team sub-graphs.

    supervisor_dispatch → research_team [sub-graph]
                        → analytics_team [sub-graph] → executive_summary
    """
    return plan(supervision_strategique())


# ── Hierarchical Supervisor Executor ─────────────────────────────────────────


class HierarchicalSupervisorExecutor(MockExecutor):
    """Executor handling nodes across top-level graph and nested subgraphs."""

    def __init__(self) -> None:
        super().__init__()
        self._analysis_iter = 0

    def run(
        self,
        node_id: str,
        objective: str,
        inputs: dict[str, Any],
        attempt: int = 1,
    ) -> ExecutionResult:
        # Top-Level Nodes
        if node_id == "supervisor_dispatch":
            return ExecutionResult(
                output={
                    "plan": (
                        "Plan d'orchestration hiérarchique déployé : "
                        "Recherche -> Analyse -> Synthèse"
                    ),
                    "delegated_teams": ["research_team", "analytics_team"],
                    "priority": "HIGH",
                },
                cost=0.002,
                latency_ms=45.0,
                confidence=1.0,
            )
        elif node_id == "executive_summary":
            return ExecutionResult(
                output={
                    "summary": "Briefing Exécutif validé par le Superviseur.",
                    "key_findings": [
                        "Croissance validée sur le segment Entreprise (+18%).",
                        "Audit de conformité modèle réussi avec indice 0.96.",
                    ],
                    "recommendation": "GO pour déploiement en production T2.",
                },
                cost=0.004,
                latency_ms=80.0,
                confidence=0.98,
            )

        # Research Team Subgraph Nodes
        elif node_id == "research_init":
            return ExecutionResult(
                output={"status": "research_initialized", "sources": ["web", "sql_db"]},
                cost=0.0005,
                latency_ms=15.0,
                confidence=1.0,
            )
        elif node_id == "web_search_agent":
            return ExecutionResult(
                output={
                    "articles_found": 8,
                    "market_trend": "Forte demande sur l'IA générative d'entreprise.",
                },
                cost=0.001,
                latency_ms=50.0,
                confidence=0.97,
            )
        elif node_id == "db_extractor_agent":
            return ExecutionResult(
                output={
                    "internal_kpis": {"arr_growth": 0.24, "nps": 68},
                    "records_analyzed": 15000,
                },
                cost=0.001,
                latency_ms=40.0,
                confidence=0.99,
            )
        elif node_id == "merge_research":
            return ExecutionResult(
                output={
                    "consolidated_dossier": (
                        "Dossier de recherche consolidé (Web + Données internes)."
                    ),
                    "total_sources": 2,
                },
                cost=0.0015,
                latency_ms=30.0,
                confidence=0.98,
            )

        # Analytics Team Subgraph Nodes
        elif node_id == "model_analysis":
            self._analysis_iter += 1
            return ExecutionResult(
                output={
                    "iteration": self._analysis_iter,
                    "forecast_roi": 2.4 + (self._analysis_iter * 0.2),
                    "confidence_interval": "95%",
                },
                cost=0.003,
                latency_ms=75.0,
                confidence=0.95,
            )
        elif node_id == "quality_audit":
            is_valid = self._analysis_iter >= 2
            return ExecutionResult(
                output={
                    "quality_index": 0.96 if is_valid else 0.74,
                    "needs_recalculation": not is_valid,
                    "is_valid": is_valid,
                },
                cost=0.002,
                latency_ms=40.0,
                confidence=0.96,
            )
        elif node_id == "formatted_insights":
            return ExecutionResult(
                output={
                    "insights": [
                        "ROI projeté à 2.8x",
                        "Risque de volatilité maîtrisé à < 5%",
                    ],
                    "status": "ready_for_executive",
                },
                cost=0.001,
                latency_ms=25.0,
                confidence=1.0,
            )

        raise ExecutionError(node_id=node_id, reason=f"Nœud inconnu: {node_id}")
