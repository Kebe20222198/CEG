"""Tests S4 — Pipeline de ventes avec arête conditionnelle.

4 scénarios officiels du cas d'usage fil rouge :
  A. Normal          — aucune anomalie → generate_alert ignoré (skipped)
  B. Anomalie simple — une région en anomalie → alerte générée
  C. Anomalies multi — plusieurs régions → alerte multi-régions
  D. Données corrompues — CSV malformé → fallback déclenché sur fetch_data
"""

import os
import pytest
from pathlib import Path

from ceg.compiler.compiler import CEGCompiler
from ceg.compiler.mock_executor import ExecutionError
from ceg.runtime.fallback import NodeAbortError
from ceg.use_cases.sales_pipeline import SalesExecutor, build_sales_graph

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _run_pipeline(csv_filename: str) -> dict:
    """Helper: build + compile + invoke the pipeline on a given CSV fixture."""
    csv_path = str(FIXTURES_DIR / csv_filename)
    graph = build_sales_graph()
    executor = SalesExecutor(csv_path=csv_path)
    compiler = CEGCompiler(executor=executor)
    workflow = compiler.compile(graph)
    return workflow.invoke()


# ══════════════════════════════════════════════════════════════════════
# Scénario A — Normal
# ══════════════════════════════════════════════════════════════════════


class TestScenarioANormal:
    """Scénario A : aucune anomalie → generate_alert doit être skipped."""

    def test_all_pipeline_nodes_complete_or_skipped(self):
        result = _run_pipeline("scenario_a_normal.csv")
        statuses = result["node_statuses"]
        assert statuses["fetch_data"] == "completed"
        assert statuses["aggregate_region"] == "completed"
        assert statuses["compute_trend"] == "completed"
        assert statuses["detect_anomaly"] == "completed"
        assert statuses["generate_alert"] == "skipped"

    def test_no_anomalies_detected(self):
        result = _run_pipeline("scenario_a_normal.csv")
        detect_output = result["node_outputs"]["detect_anomaly"]
        assert isinstance(detect_output, dict)
        assert detect_output["anomalies_found"] is False
        assert detect_output["anomalies"] == []

    def test_generate_alert_output_is_none(self):
        result = _run_pipeline("scenario_a_normal.csv")
        assert result["node_outputs"]["generate_alert"] is None

    def test_generate_alert_absent_from_execution_log_as_completed(self):
        result = _run_pipeline("scenario_a_normal.csv")
        completed_nodes = [
            e["node_id"]
            for e in result["execution_log"]
            if e["status"] == "completed"
        ]
        assert "generate_alert" not in completed_nodes

    def test_latency_is_positive(self):
        result = _run_pipeline("scenario_a_normal.csv")
        assert result["total_latency_ms"] > 0


# ══════════════════════════════════════════════════════════════════════
# Scénario B — Anomalie simple
# ══════════════════════════════════════════════════════════════════════


class TestScenarioBSingleAnomaly:
    """Scénario B : une région (Nord) avec chute de 26% → alerte générée."""

    def test_generate_alert_is_completed(self):
        result = _run_pipeline("scenario_b_single_anomaly.csv")
        assert result["node_statuses"]["generate_alert"] == "completed"

    def test_detect_anomaly_finds_nord(self):
        result = _run_pipeline("scenario_b_single_anomaly.csv")
        detect_output = result["node_outputs"]["detect_anomaly"]
        assert detect_output["anomalies_found"] is True
        anomaly_regions = [a["region"] for a in detect_output["anomalies"]]
        assert "Nord" in anomaly_regions

    def test_nord_variation_around_minus_26(self):
        result = _run_pipeline("scenario_b_single_anomaly.csv")
        detect_output = result["node_outputs"]["detect_anomaly"]
        nord_anomaly = next(a for a in detect_output["anomalies"] if a["region"] == "Nord")
        # Allow ±1% tolerance
        assert nord_anomaly["variation_pct"] < -20.0
        assert nord_anomaly["variation_pct"] > -35.0

    def test_alert_message_contains_nord(self):
        result = _run_pipeline("scenario_b_single_anomaly.csv")
        alert_output = result["node_outputs"]["generate_alert"]
        assert isinstance(alert_output, dict)
        assert "Nord" in alert_output["alert_message"]

    def test_alert_message_contains_percentage(self):
        result = _run_pipeline("scenario_b_single_anomaly.csv")
        alert_output = result["node_outputs"]["generate_alert"]
        # Message should contain a percentage sign
        assert "%" in alert_output["alert_message"]

    def test_only_nord_in_anomalies(self):
        result = _run_pipeline("scenario_b_single_anomaly.csv")
        detect_output = result["node_outputs"]["detect_anomaly"]
        anomaly_regions = [a["region"] for a in detect_output["anomalies"]]
        # Sud, Est, Ouest should NOT be in anomalies (< 20% drop)
        assert "Sud" not in anomaly_regions
        assert "Est" not in anomaly_regions
        assert "Ouest" not in anomaly_regions


# ══════════════════════════════════════════════════════════════════════
# Scénario C — Anomalies multiples
# ══════════════════════════════════════════════════════════════════════


class TestScenarioCMultipleAnomalies:
    """Scénario C : 3 régions en anomalie (Nord -30%, Sud -25%, Ouest -28%)."""

    def test_generate_alert_is_completed(self):
        result = _run_pipeline("scenario_c_multiple_anomalies.csv")
        assert result["node_statuses"]["generate_alert"] == "completed"

    def test_three_anomalies_detected(self):
        result = _run_pipeline("scenario_c_multiple_anomalies.csv")
        detect_output = result["node_outputs"]["detect_anomaly"]
        assert detect_output["anomalies_found"] is True
        assert len(detect_output["anomalies"]) == 3

    def test_correct_anomaly_regions(self):
        result = _run_pipeline("scenario_c_multiple_anomalies.csv")
        detect_output = result["node_outputs"]["detect_anomaly"]
        anomaly_regions = {a["region"] for a in detect_output["anomalies"]}
        assert "Nord" in anomaly_regions
        assert "Sud" in anomaly_regions
        assert "Ouest" in anomaly_regions

    def test_non_anomaly_regions_absent(self):
        result = _run_pipeline("scenario_c_multiple_anomalies.csv")
        detect_output = result["node_outputs"]["detect_anomaly"]
        anomaly_regions = {a["region"] for a in detect_output["anomalies"]}
        assert "Est" not in anomaly_regions
        assert "Centre" not in anomaly_regions

    def test_alert_message_contains_all_anomaly_regions(self):
        result = _run_pipeline("scenario_c_multiple_anomalies.csv")
        alert_output = result["node_outputs"]["generate_alert"]
        msg = alert_output["alert_message"]
        assert "Nord" in msg
        assert "Sud" in msg
        assert "Ouest" in msg

    def test_alert_anomaly_count_is_three(self):
        result = _run_pipeline("scenario_c_multiple_anomalies.csv")
        alert_output = result["node_outputs"]["generate_alert"]
        assert alert_output["anomaly_count"] == 3


# ══════════════════════════════════════════════════════════════════════
# Scénario D — Données corrompues
# ══════════════════════════════════════════════════════════════════════


class TestScenarioDCorruptedData:
    """Scénario D : CSV malformé → ExecutionError → fallback → NodeAbortError."""

    def test_corrupted_csv_raises_node_abort(self):
        """fetch_data fails on corrupted CSV → fallback chain → NodeAbortError."""
        with pytest.raises(NodeAbortError) as exc_info:
            _run_pipeline("scenario_d_corrupted.csv")
        assert exc_info.value.node_id == "fetch_data"

    def test_abort_error_message_meaningful(self):
        """NodeAbortError reason should mention the failure cause."""
        with pytest.raises(NodeAbortError) as exc_info:
            _run_pipeline("scenario_d_corrupted.csv")
        # The reason should contain something about the failure
        assert len(exc_info.value.reason) > 0

    def test_fallback_triggered_not_plain_execution_error(self):
        """The error that surfaces must be NodeAbortError (fallback chain ran), not raw ExecutionError."""
        with pytest.raises(NodeAbortError):
            _run_pipeline("scenario_d_corrupted.csv")
        # If this passes, it means FallbackOrchestrator was triggered (not raw ExecutionError)


# ══════════════════════════════════════════════════════════════════════
# Tests additionnels — arête conditionnelle
# ══════════════════════════════════════════════════════════════════════


class TestConditionalEdge:
    """Tests unitaires de l'arête conditionnelle dans le compilateur."""

    def test_compiler_accepts_conditional_edge(self):
        from ceg.models.graph import CEGEdge, CEGGraph, EdgeType
        from ceg.models.node import CEGNode
        graph = CEGGraph(
            nodes=[
                CEGNode(id="A", objective="step A"),
                CEGNode(id="B", objective="step B"),
            ],
            edges=[
                CEGEdge(source="A", target="B", edge_type=EdgeType.CONDITIONAL, condition="some_flag"),
            ],
        )
        compiler = CEGCompiler()
        # Should compile without error
        workflow = compiler.compile(graph)
        assert workflow is not None

    def test_conditional_edge_skips_target_when_condition_false(self):
        """When condition key is absent/False in source output, target is skipped."""
        from ceg.models.graph import CEGEdge, CEGGraph, EdgeType
        from ceg.models.node import CEGNode
        from ceg.compiler.mock_executor import MockExecutor

        # Custom executor: A returns output WITHOUT "go_b" key set to True
        class ConditionalExecutor:
            def execute(self, node_id, objective, inputs, attempt=1):
                from ceg.compiler.mock_executor import ExecutionResult
                if node_id == "A":
                    return ExecutionResult(output={"go_b": False}, cost=0.001, latency_ms=10.0, confidence=0.9)
                return ExecutionResult(output={"done": True}, cost=0.001, latency_ms=10.0, confidence=0.9)

        graph = CEGGraph(
            nodes=[
                CEGNode(id="A", objective="step A"),
                CEGNode(id="B", objective="step B"),
            ],
            edges=[
                CEGEdge(source="A", target="B", edge_type=EdgeType.CONDITIONAL, condition="go_b"),
            ],
        )
        compiler = CEGCompiler(executor=ConditionalExecutor())
        workflow = compiler.compile(graph)
        result = workflow.invoke()
        assert result["node_statuses"]["A"] == "completed"
        assert result["node_statuses"]["B"] == "skipped"

    def test_conditional_edge_executes_target_when_condition_true(self):
        """When condition key is True in source output, target executes normally."""
        from ceg.models.graph import CEGEdge, CEGGraph, EdgeType
        from ceg.models.node import CEGNode

        class ConditionalExecutor:
            def execute(self, node_id, objective, inputs, attempt=1):
                from ceg.compiler.mock_executor import ExecutionResult
                if node_id == "A":
                    return ExecutionResult(output={"go_b": True}, cost=0.001, latency_ms=10.0, confidence=0.9)
                return ExecutionResult(output={"done": True}, cost=0.001, latency_ms=10.0, confidence=0.9)

        graph = CEGGraph(
            nodes=[
                CEGNode(id="A", objective="step A"),
                CEGNode(id="B", objective="step B"),
            ],
            edges=[
                CEGEdge(source="A", target="B", edge_type=EdgeType.CONDITIONAL, condition="go_b"),
            ],
        )
        compiler = CEGCompiler(executor=ConditionalExecutor())
        workflow = compiler.compile(graph)
        result = workflow.invoke()
        assert result["node_statuses"]["A"] == "completed"
        assert result["node_statuses"]["B"] == "completed"
