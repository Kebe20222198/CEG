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
from ceg.models.graph import CEGEdge, CEGGraph, EdgeType
from ceg.models.node import CEGNode, ModelTierHint
from ceg.models.task import CognitiveTask


# ── Subgraph 1: Research Team ────────────────────────────────────────────────

def build_research_team_subgraph() -> CEGGraph:
    """Build the inner Research Team subgraph with parallel sources.

         ┌──→ web_search_agent ──┐
    init ┤                       ├──→ merge_research
         └──→ db_extractor_agent ┘
    """
    nodes = [
        CEGNode(
            id="research_init",
            objective="Initialiser les paramètres de recherche contextuelle",
            task=CognitiveTask(objective="Paramétrage des sources documentaires"),
            model_tier_hint=ModelTierHint.FAST,
        ),
        CEGNode(
            id="web_search_agent",
            objective="Recherche documentaire web et veille concurrentielle",
            dependencies=["research_init"],
            task=CognitiveTask(objective="Extraction web et actualités"),
            required_capabilities=["web_search", "information_retrieval"],
            model_tier_hint=ModelTierHint.FAST,
        ),
        CEGNode(
            id="db_extractor_agent",
            objective="Extraction des métriques internes et historique SQL",
            dependencies=["research_init"],
            task=CognitiveTask(objective="Interrogation des bases internes"),
            required_capabilities=["data_retrieval"],
            model_tier_hint=ModelTierHint.FAST,
        ),
        CEGNode(
            id="merge_research",
            objective="Consolider et dédupliquer les sources de recherche",
            dependencies=["web_search_agent", "db_extractor_agent"],
            task=CognitiveTask(objective="Synthèse des éléments de recherche"),
            required_capabilities=["data_analysis"],
            model_tier_hint=ModelTierHint.BALANCED,
        ),
    ]

    edges = [
        CEGEdge(source="research_init", target="web_search_agent", edge_type=EdgeType.PARALLEL),
        CEGEdge(source="research_init", target="db_extractor_agent", edge_type=EdgeType.PARALLEL),
        CEGEdge(source="web_search_agent", target="merge_research", edge_type=EdgeType.SEQUENTIAL),
        CEGEdge(source="db_extractor_agent", target="merge_research", edge_type=EdgeType.SEQUENTIAL),
    ]

    return CEGGraph(nodes=nodes, edges=edges)


# ── Subgraph 2: Analytics & Quality Team ─────────────────────────────────────

def build_analytics_team_subgraph() -> CEGGraph:
    """Build the inner Analytics Team subgraph with self-critique loop.

    model_analysis ──→ quality_audit ──[LOOP max 2]──┐
          ↑                                          │
          └──────────────────────────────────────────┘
                              │
                              └──[SEQUENTIAL]──→ formatted_insights
    """
    nodes = [
        CEGNode(
            id="model_analysis",
            objective="Calculer les projections financières et analyses d'impact",
            task=CognitiveTask(objective="Modélisation statistique et financière"),
            model_tier_hint=ModelTierHint.QUALITY,
        ),
        CEGNode(
            id="quality_audit",
            objective="Audit de cohérence et vérification des hypothèses",
            dependencies=["model_analysis"],
            task=CognitiveTask(objective="Vérification méthodologique"),
            required_capabilities=["reasoning", "evaluation"],
            model_tier_hint=ModelTierHint.QUALITY,
        ),
        CEGNode(
            id="formatted_insights",
            objective="Mise en forme des recommandations actionnables",
            dependencies=["quality_audit"],
            task=CognitiveTask(objective="Formatage exécutif"),
            model_tier_hint=ModelTierHint.FAST,
        ),
    ]

    edges = [
        CEGEdge(source="model_analysis", target="quality_audit", edge_type=EdgeType.SEQUENTIAL),
        CEGEdge(
            source="quality_audit",
            target="model_analysis",
            edge_type=EdgeType.LOOP,
            condition="needs_recalculation",
            loop_max_iterations=2,
        ),
        CEGEdge(
            source="quality_audit",
            target="formatted_insights",
            edge_type=EdgeType.SEQUENTIAL,
            condition="is_valid",
        ),
    ]

    return CEGGraph(nodes=nodes, edges=edges)


# ── Top-Level Hierarchical Multi-Agent Graph ─────────────────────────────────

def build_hierarchical_supervisor_graph() -> CEGGraph:
    """Build the top-level supervisor graph with 2 team subgraphs.

    supervisor_dispatch
          │
          ├──→ research_team_subgraph [Composite Subgraph Node]
          │             │
          │             ↓
          └──→ analytics_team_subgraph [Composite Subgraph Node]
                        │
                        ↓
                 executive_summary
    """
    research_sub = build_research_team_subgraph()
    analytics_sub = build_analytics_team_subgraph()

    nodes = [
        CEGNode(
            id="supervisor_dispatch",
            objective="Superviser l'analyse globale et déléguer aux équipes spécialisées",
            task=CognitiveTask(objective="Planification stratégique et orchestration hiérarchique"),
            model_tier_hint=ModelTierHint.QUALITY,
        ),
        CEGNode(
            id="research_team",
            objective="Équipe Recherche : Collecte multi-sources et veille",
            dependencies=["supervisor_dispatch"],
            task=CognitiveTask(objective="Exécution du sous-graphe de recherche documentaire"),
            subgraph=research_sub,
            model_tier_hint=ModelTierHint.BALANCED,
        ),
        CEGNode(
            id="analytics_team",
            objective="Équipe Analyse & Risques : Modélisation et audit itératif",
            dependencies=["research_team"],
            task=CognitiveTask(objective="Exécution du sous-graphe d'analyse prédictive"),
            subgraph=analytics_sub,
            model_tier_hint=ModelTierHint.QUALITY,
        ),
        CEGNode(
            id="executive_summary",
            objective="Consolider la synthèse décisionnelle finale pour la direction",
            dependencies=["analytics_team"],
            task=CognitiveTask(objective="Rédaction du briefing exécutif final"),
            model_tier_hint=ModelTierHint.QUALITY,
        ),
    ]

    edges = [
        CEGEdge(
            source="supervisor_dispatch",
            target="research_team",
            edge_type=EdgeType.SEQUENTIAL,
        ),
        CEGEdge(
            source="research_team",
            target="analytics_team",
            edge_type=EdgeType.SEQUENTIAL,
        ),
        CEGEdge(
            source="analytics_team",
            target="executive_summary",
            edge_type=EdgeType.SEQUENTIAL,
        ),
    ]

    return CEGGraph(nodes=nodes, edges=edges)


# ── Hierarchical Supervisor Executor ─────────────────────────────────────────

class HierarchicalSupervisorExecutor(MockExecutor):
    """Executor handling nodes across top-level graph and nested subgraphs."""

    def __init__(self) -> None:
        super().__init__()
        self._analysis_iter = 0

    def execute(
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
                    "plan": "Plan d'orchestration hiérarchique déployé : Recherche -> Analyse -> Synthèse",
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
                    "consolidated_dossier": "Dossier de recherche consolidé (Web + Données internes).",
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
                    "insights": ["ROI projeté à 2.8x", "Risque de volatilité maîtrisé à < 5%"],
                    "status": "ready_for_executive",
                },
                cost=0.001,
                latency_ms=25.0,
                confidence=1.0,
            )

        raise ExecutionError(node_id=node_id, reason=f"Nœud inconnu: {node_id}")
