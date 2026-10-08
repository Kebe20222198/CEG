"""Tri des tickets du support client — exemple de workflow écrit par un utilisateur.

Déposer ce fichier dans ``workflows/`` suffit :
  - il apparaît dans le Studio (onglet Workflows), avec son graphe et son code ;
  - ``ceg validate tri_tickets_support`` vérifie la déclaration sans l'exécuter ;
  - ``ceg run tri_tickets_support`` l'exécute dans le terminal.

Le fichier contient deux choses : la **déclaration** (quoi faire, sous quelles
contraintes) et l'**exécuteur** (comment faire chaque étape). Ici les étapes
sont écrites en Python pur ; un appel à un LLM se brancherait au même endroit.
"""

from typing import Any

from ceg import (
    CognitiveTask,
    ExecutionResult,
    MockExecutor,
    SubTask,
    TaskConstraint,
    workflow,
)

URGENT_WORDS = ("panne", "bloqué", "urgent", "impossible")

CATEGORIES = {
    "facture": "facturation",
    "paiement": "facturation",
    "mot de passe": "compte",
    "connexion": "compte",
}

EXAMPLE_TICKETS = [
    {"id": "T-101", "texte": "Ma facture de mars est en double."},
    {"id": "T-102", "texte": "Panne totale : impossible d'accéder au service."},
    {"id": "T-103", "texte": "J'ai oublié mon mot de passe."},
    {"id": "T-104", "texte": "Pouvez-vous ajouter un export PDF ?"},
]


def _category(text: str) -> str:
    lowered = text.lower()
    for keyword, category in CATEGORIES.items():
        if keyword in lowered:
            return category
    return "autre"


class EtapesTickets(MockExecutor):
    """Implémentation des étapes du tri (une méthode ``run`` par workflow)."""

    def run(
        self, node_id: str, objective: str, inputs: dict[str, Any], attempt: int = 1
    ) -> ExecutionResult:
        tickets: list[dict[str, str]] = inputs.get("tickets", [])
        if node_id == "lire_tickets":
            output: dict[str, Any] = {"nombre": len(tickets)}
        elif node_id == "classer":
            output = {"categories": {t["id"]: _category(t["texte"]) for t in tickets}}
        elif node_id == "detecter_urgences":
            urgents = [
                t["id"]
                for t in tickets
                if any(word in t["texte"].lower() for word in URGENT_WORDS)
            ]
            output = {"urgents": urgents, "a_des_urgences": bool(urgents)}
        elif node_id == "escalader":
            output = {
                "escalades": inputs["detecter_urgences"]["urgents"],
                "equipe": "astreinte",
            }
        elif node_id == "rediger_synthese":
            categories = inputs["classer"]["categories"]
            urgents = inputs["detecter_urgences"]["urgents"]
            output = {
                "synthese": (
                    f"{len(tickets)} tickets : "
                    + ", ".join(
                        f"{c} ({list(categories.values()).count(c)})"
                        for c in sorted(set(categories.values()))
                    )
                    + f". Urgents : {', '.join(urgents) or 'aucun'}."
                )
            }
        else:
            return super().run(node_id, objective, inputs, attempt)
        return ExecutionResult(output=output, cost=0.0, latency_ms=10.0, confidence=0.9)


@workflow(executor=EtapesTickets, default_inputs={"tickets": EXAMPLE_TICKETS})
def tri_tickets_support() -> CognitiveTask:
    """Trier les tickets du support, escalader les urgences, résumer la journée."""
    return CognitiveTask(
        name="tri_tickets_support",
        objective=(
            "Classer les tickets du support, escalader les urgences à l'astreinte "
            "et rédiger une synthèse."
        ),
        task_constraints=TaskConstraint(
            max_cost_usd=0.10, max_latency_seconds=5.0, min_quality_score=0.0
        ),
        tools_allowed=["ticketing_api", "pager"],
        subtasks=[
            SubTask(
                id="lire_tickets",
                objective="Lire les tickets ouverts",
                required_capabilities=["data_retrieval"],
                model_tier_hint="fast",
                tools=["ticketing_api"],
            ),
            # Ces deux étapes ne dépendent que de la lecture : elles
            # s'exécutent en parallèle, sans qu'on le demande.
            SubTask(
                id="classer",
                objective="Classer chaque ticket par catégorie",
                dependencies=["lire_tickets"],
                required_capabilities=["reasoning"],
                model_tier_hint="balanced",
            ),
            SubTask(
                id="detecter_urgences",
                objective="Repérer les tickets urgents",
                dependencies=["lire_tickets"],
                required_capabilities=["reasoning"],
                model_tier_hint="quality",
            ),
            # N'escalader que s'il y a des urgences.
            SubTask(
                id="escalader",
                objective="Prévenir l'équipe d'astreinte",
                run_if="detecter_urgences.a_des_urgences",
                required_capabilities=["notification"],
                model_tier_hint="fast",
                tools=["pager"],
            ),
            SubTask(
                id="rediger_synthese",
                objective="Rédiger la synthèse du jour",
                dependencies=["classer", "detecter_urgences"],
                required_capabilities=["summarization"],
                model_tier_hint="balanced",
            ),
        ],
    )
