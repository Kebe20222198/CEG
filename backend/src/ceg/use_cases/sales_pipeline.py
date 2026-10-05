"""Sales Pipeline — Cas d'usage fil rouge CEG Semaine 4.

Pipeline d'agrégation des ventes par région avec alerte de chute de volume.
Objectif métier : détecter automatiquement toute région dont les ventes ont
chuté de plus de 20 % par rapport au mois précédent et générer une alerte.

Graphe : fetch_data → aggregate_region → compute_trend → detect_anomaly
         →[CONDITIONAL: anomalies_found]→ generate_alert

Run avec : python -m ceg.use_cases.sales_pipeline
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

from ceg.compiler.compiler import CEGCompiler
from ceg.compiler.mock_executor import ExecutionError, ExecutionResult, MockExecutor
from ceg.models.graph import CEGGraph
from ceg.models.task import CognitiveTask, SubTask, TaskConstraint
from ceg.planner import plan

# ── Minimal SDK decorator ─────────────────────────────────────────────────────


_TaskFn = TypeVar("_TaskFn", bound=Callable[..., Any])


class _CEGRegistry:
    """Minimal task registry supporting the @ceg.task() decorator pattern."""

    def __init__(self) -> None:
        self._tasks: dict[str, Callable[..., Any]] = {}

    def task(self) -> Callable[[_TaskFn], _TaskFn]:
        """Decorator that registers the decorated function as a CEG task."""

        def decorator(fn: _TaskFn) -> _TaskFn:
            self._tasks[fn.__name__] = fn
            return fn

        return decorator


ceg = _CEGRegistry()


# ── SalesExecutor ─────────────────────────────────────────────────────────────


class SalesExecutor(MockExecutor):
    """Executor that dispatches to the real sales business logic functions.

    Unlike MockExecutor, each node runs actual Python business logic
    (CSV reading, aggregation, trend computation, anomaly detection, alerting).

    Args:
        csv_path: Path to the transactions CSV file. Injected into
            ``fetch_data`` inputs automatically.
    """

    def __init__(
        self,
        csv_path: str | None = None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__()
        self.csv_path = csv_path
        # Date printed in the alert; inject a fixed clock for reproducible runs.
        self.clock = clock

    def run(
        self,
        node_id: str,
        objective: str,
        inputs: dict[str, Any],
        attempt: int = 1,
    ) -> ExecutionResult:
        """Dispatch execution to the appropriate business function."""
        from ceg.use_cases.executors.sales_executors import (
            execute_aggregate_region,
            execute_compute_trend,
            execute_detect_anomaly,
            execute_fetch_data,
            execute_generate_alert,
        )

        if node_id == "fetch_data":
            enriched: dict[str, Any] = dict(inputs)
            if self.csv_path is not None:
                enriched["csv_path"] = self.csv_path
            return execute_fetch_data(node_id, objective, enriched)
        elif node_id == "aggregate_region":
            return execute_aggregate_region(node_id, objective, inputs)
        elif node_id == "compute_trend":
            return execute_compute_trend(node_id, objective, inputs)
        elif node_id == "detect_anomaly":
            return execute_detect_anomaly(node_id, objective, inputs)
        elif node_id == "generate_alert":
            return execute_generate_alert(node_id, objective, inputs, now=self.clock())
        else:
            raise ExecutionError(node_id=node_id, reason=f"Nœud inconnu: {node_id}")


# ── Task definition (SDK declarative style) ───────────────────────────────────


@ceg.task()
def analyse_ventes_alertes() -> CognitiveTask:
    """Définition déclarative du pipeline d'analyse des ventes et alertes."""
    return CognitiveTask(
        name="analyse_ventes_alertes",
        objective=(
            "Analyser les ventes par région et alerter en cas de chute "
            "de volume supérieure à 20% par rapport au mois précédent."
        ),
        task_constraints=TaskConstraint(
            max_cost_usd=0.50,
            max_latency_seconds=15.0,
            min_quality_score=0.85,
        ),
        tools_allowed=["sql_query", "send_alert", "data_aggregator"],
        subtasks=[
            SubTask(
                id="fetch_data",
                objective="Charger et valider les données CSV de transactions",
                required_capabilities=["data_reading", "validation"],
                model_tier_hint="fast",
            ),
            SubTask(
                id="aggregate_region",
                objective="Agréger les ventes par région et par mois",
                required_capabilities=["aggregation", "computation"],
                model_tier_hint="fast",
                dependencies=["fetch_data"],
                tools=["data_aggregator"],
            ),
            SubTask(
                id="compute_trend",
                objective="Calculer l'évolution mois-sur-mois par région",
                required_capabilities=["trend_analysis", "computation"],
                model_tier_hint="balanced",
                dependencies=["aggregate_region"],
            ),
            SubTask(
                id="detect_anomaly",
                objective="Identifier les régions avec une chute de volume > 20%",
                required_capabilities=["anomaly_detection", "reasoning"],
                model_tier_hint="quality",
                dependencies=["compute_trend"],
            ),
            SubTask(
                id="generate_alert",
                objective="Rédiger et envoyer l'alerte pour les régions en anomalie",
                required_capabilities=["notification", "summarization"],
                model_tier_hint="balanced",
                # Alerter uniquement si des anomalies ont été trouvées.
                run_if="detect_anomaly.anomalies_found",
                tools=["send_alert"],
            ),
        ],
    )


# ── Graph builder ─────────────────────────────────────────────────────────────


def build_sales_graph() -> CEGGraph:
    """Plan the sales pipeline from its declaration.

    Graph topology (decided by the planner):
        fetch_data → aggregate_region → compute_trend → detect_anomaly
        →[CONDITIONAL: anomalies_found]→ generate_alert
    """
    return plan(analyse_ventes_alertes())


def build_sales_executor(csv_path: str | None = None) -> SalesExecutor:
    """Create a SalesExecutor configured with the given CSV path."""
    return SalesExecutor(csv_path=csv_path)


# ── CLI entry point ───────────────────────────────────────────────────────────


def main(csv_path: str | None = None) -> dict[str, Any]:
    """Build, compile, and execute the sales pipeline end-to-end.

    Args:
        csv_path: Path to the transactions CSV file. If None, uses a default
            example path (will raise ExecutionError if file doesn't exist).

    Returns:
        Final workflow state dict.
    """
    import os

    if csv_path is None:
        # Default to scenario_b for a demo with anomaly detection
        csv_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "tests",
            "fixtures",
            "scenario_b_single_anomaly.csv",
        )
        csv_path = os.path.normpath(csv_path)

    print("=" * 65)
    print(" CEG Sales Pipeline — Agrégation Ventes + Alerte (Semaine 4)")
    print("=" * 65)

    # 1. Build
    graph = build_sales_graph()
    print(f"\n✓ Graphe CEG : {len(graph.nodes)} nœuds, {len(graph.edges)} arêtes")

    # 2. Compile
    executor = SalesExecutor(csv_path=csv_path)
    compiler = CEGCompiler(executor=executor)
    workflow = compiler.compile(graph)
    print("✓ Compilé vers LangGraph")
    print(f"  Ordre d'exécution : {workflow.metadata['execution_order']}")

    # 3. Execute
    print("\n— Exécution du workflow —\n")
    result = workflow.invoke()

    # 4. Display
    print("\n— Résultats —\n")
    for entry in result.get("execution_log", []):
        status_icon = (
            "✓"
            if entry["status"] == "completed"
            else ("⏭" if entry["status"] == "skipped" else "✗")
        )
        print(
            f"  {status_icon} [{entry['node_id']}] status={entry['status']}  "
            f"cost={entry.get('cost', 0):.4f}  "
            f"latency={entry.get('latency_ms', 0):.1f}ms"
        )

    print(f"\n  Coût total    : {result.get('total_cost', 0):.4f} USD")
    print(f"  Latence totale: {result.get('total_latency_ms', 0):.1f} ms")

    # Show alert if generated
    detect_output = result.get("node_outputs", {}).get("detect_anomaly", {})
    if isinstance(detect_output, dict) and detect_output.get("anomalies_found"):
        alert_output = result.get("node_outputs", {}).get("generate_alert", {})
        if isinstance(alert_output, dict):
            print("\n— Alerte générée —\n")
            print(alert_output.get("alert_message", ""))
    else:
        print("\n✓ Aucune anomalie détectée — alerte ignorée (generate_alert: skipped)")

    print("=" * 65)
    return result


if __name__ == "__main__":
    main()
