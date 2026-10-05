"""Tests for CEG Compiler — PARALLEL edge support (fan-out / fan-in).

Validates that the compiler correctly translates PARALLEL edges into
concurrent LangGraph execution, where multiple nodes fan-out from a
single source and converge at a join node.
"""

from collections import Counter
from typing import Any

import pytest

from ceg.compiler.compiler import CEGCompiler
from ceg.compiler.mock_executor import MockExecutor
from ceg.models.graph import CEGEdge, CEGGraph, EdgeType
from ceg.models.node import CEGNode
from ceg.runtime.decision_engine import DEFAULT_MODEL_REGISTRY, RuntimeDecisionEngine

# ── Helpers ──────────────────────────────────────────────────────────


def _fan_out_graph() -> CEGGraph:
    """Build a fan-out/fan-in graph: start → {B, C} → merge.

         ┌──→ B ──┐
    A ───┤         ├──→ D
         └──→ C ──┘
    """
    return CEGGraph(
        nodes=[
            CEGNode(id="A", objective="Start node"),
            CEGNode(id="B", objective="Parallel branch 1", dependencies=["A"]),
            CEGNode(id="C", objective="Parallel branch 2", dependencies=["A"]),
            CEGNode(id="D", objective="Merge results", dependencies=["B", "C"]),
        ],
        edges=[
            CEGEdge(source="A", target="B", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="A", target="C", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="B", target="D", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="C", target="D", edge_type=EdgeType.PARALLEL),
        ],
    )


def _triple_fan_out_graph() -> CEGGraph:
    """Build a triple fan-out: A → {B, C, D} → E.

         ┌──→ B ──┐
    A ───┤──→ C ──├──→ E
         └──→ D ──┘
    """
    return CEGGraph(
        nodes=[
            CEGNode(id="A", objective="Source"),
            CEGNode(id="B", objective="Branch 1", dependencies=["A"]),
            CEGNode(id="C", objective="Branch 2", dependencies=["A"]),
            CEGNode(id="D", objective="Branch 3", dependencies=["A"]),
            CEGNode(id="E", objective="Join", dependencies=["B", "C", "D"]),
        ],
        edges=[
            CEGEdge(source="A", target="B", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="A", target="C", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="A", target="D", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="B", target="E", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="C", target="E", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="D", target="E", edge_type=EdgeType.PARALLEL),
        ],
    )


def _sequential_join_graph() -> CEGGraph:
    """Fan-out with a SEQUENTIAL join: A →{B, C, D}→ E.

    Mirrors the shape used by the real demo pipelines, where the fan-out
    edges are PARALLEL but the converging edges are declared SEQUENTIAL.

         ┌──→ B ──┐
    A ───┤──→ C ──├──→ E
         └──→ D ──┘
    """
    return CEGGraph(
        nodes=[
            CEGNode(id="A", objective="Source"),
            CEGNode(id="B", objective="Branch 1", dependencies=["A"]),
            CEGNode(id="C", objective="Branch 2", dependencies=["A"]),
            CEGNode(id="D", objective="Branch 3", dependencies=["A"]),
            CEGNode(id="E", objective="Join", dependencies=["B", "C", "D"]),
        ],
        edges=[
            CEGEdge(source="A", target="B", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="A", target="C", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="A", target="D", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="B", target="E", edge_type=EdgeType.SEQUENTIAL),
            CEGEdge(source="C", target="E", edge_type=EdgeType.SEQUENTIAL),
            CEGEdge(source="D", target="E", edge_type=EdgeType.SEQUENTIAL),
        ],
    )


def _sequential_then_parallel_graph() -> CEGGraph:
    """Build: A → B → {C, D} → E (mix of sequential and parallel).

    A ──→ B ──┤──→ C ──├──→ E
              └──→ D ──┘
    """
    return CEGGraph(
        nodes=[
            CEGNode(id="A", objective="Init"),
            CEGNode(id="B", objective="Process", dependencies=["A"]),
            CEGNode(id="C", objective="Branch 1", dependencies=["B"]),
            CEGNode(id="D", objective="Branch 2", dependencies=["B"]),
            CEGNode(id="E", objective="Final", dependencies=["C", "D"]),
        ],
        edges=[
            CEGEdge(source="A", target="B", edge_type=EdgeType.SEQUENTIAL),
            CEGEdge(source="B", target="C", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="B", target="D", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="C", target="E", edge_type=EdgeType.PARALLEL),
            CEGEdge(source="D", target="E", edge_type=EdgeType.PARALLEL),
        ],
    )


# ══════════════════════════════════════════════════════════════════════
# Validation tests
# ══════════════════════════════════════════════════════════════════════


class TestParallelValidation:
    """Tests that PARALLEL edges pass validation correctly."""

    def test_fan_out_graph_is_valid(self):
        """A fan-out/fan-in graph with PARALLEL edges passes validation."""
        compiler = CEGCompiler()
        graph = _fan_out_graph()
        compiler._validate(graph)

    def test_triple_fan_out_is_valid(self):
        """A 3-branch fan-out graph passes validation."""
        compiler = CEGCompiler()
        graph = _triple_fan_out_graph()
        compiler._validate(graph)

    def test_mixed_sequential_parallel_is_valid(self):
        """A graph mixing SEQUENTIAL and PARALLEL edges passes validation."""
        compiler = CEGCompiler()
        graph = _sequential_then_parallel_graph()
        compiler._validate(graph)


# ══════════════════════════════════════════════════════════════════════
# Topological sort tests
# ══════════════════════════════════════════════════════════════════════


class TestParallelTopologicalSort:
    """Tests that topological sort handles PARALLEL edges correctly."""

    def test_fan_out_order(self):
        """Fan-out graph produces valid topological order."""
        compiler = CEGCompiler()
        order = compiler._topological_sort(_fan_out_graph())
        # A must come first, D must come last
        assert order[0] == "A"
        assert order[-1] == "D"
        # B and C must come after A but before D
        assert "B" in order[1:3]
        assert "C" in order[1:3]

    def test_triple_fan_out_order(self):
        """Triple fan-out produces valid order with A first and E last."""
        compiler = CEGCompiler()
        order = compiler._topological_sort(_triple_fan_out_graph())
        assert order[0] == "A"
        assert order[-1] == "E"
        assert set(order[1:4]) == {"B", "C", "D"}


# ══════════════════════════════════════════════════════════════════════
# End-to-end execution tests
# ══════════════════════════════════════════════════════════════════════


class TestParallelExecution:
    """Tests that PARALLEL fan-out/fan-in executes all branches."""

    def test_fan_out_all_nodes_execute(self):
        """All 4 nodes in the fan-out graph execute successfully."""
        compiler = CEGCompiler()
        workflow = compiler.compile(_fan_out_graph())
        result = workflow.invoke()

        # All nodes completed
        assert result["node_statuses"]["A"] == "completed"
        assert result["node_statuses"]["B"] == "completed"
        assert result["node_statuses"]["C"] == "completed"
        assert result["node_statuses"]["D"] == "completed"

    def test_fan_out_outputs_non_empty(self):
        """All nodes in the fan-out graph produce non-empty outputs."""
        compiler = CEGCompiler()
        workflow = compiler.compile(_fan_out_graph())
        result = workflow.invoke()

        for node_id in ["A", "B", "C", "D"]:
            assert result["node_outputs"][node_id] is not None

    def test_fan_out_cost_accumulated(self):
        """Total cost is the sum of all parallel + sequential node costs."""
        compiler = CEGCompiler()
        workflow = compiler.compile(_fan_out_graph())
        result = workflow.invoke()

        assert result["total_cost"] > 0.0

    def test_fan_out_execution_log(self):
        """Execution log contains entries for all 4 nodes."""
        compiler = CEGCompiler()
        workflow = compiler.compile(_fan_out_graph())
        result = workflow.invoke()

        log_node_ids = [entry["node_id"] for entry in result["execution_log"]]
        assert set(log_node_ids) == {"A", "B", "C", "D"}

    def test_triple_fan_out_all_nodes_execute(self):
        """All 5 nodes in the triple fan-out graph execute successfully."""
        compiler = CEGCompiler()
        workflow = compiler.compile(_triple_fan_out_graph())
        result = workflow.invoke()

        for node_id in ["A", "B", "C", "D", "E"]:
            assert result["node_statuses"][node_id] == "completed"
            assert result["node_outputs"][node_id] is not None

    def test_mixed_sequential_parallel_execution(self):
        """Mixed sequential + parallel graph executes all nodes."""
        compiler = CEGCompiler()
        workflow = compiler.compile(_sequential_then_parallel_graph())
        result = workflow.invoke()

        for node_id in ["A", "B", "C", "D", "E"]:
            assert result["node_statuses"][node_id] == "completed"

    def test_metadata_has_parallel_flag(self):
        """Compilation metadata indicates parallel edges are present."""
        compiler = CEGCompiler()
        workflow = compiler.compile(_fan_out_graph())
        assert workflow.metadata["has_parallel"] is True

    def test_metadata_no_parallel_for_sequential(self):
        """Compilation metadata correctly indicates no parallel edges."""
        compiler = CEGCompiler()
        graph = CEGGraph(
            nodes=[
                CEGNode(id="A", objective="A"),
                CEGNode(id="B", objective="B", dependencies=["A"]),
            ],
            edges=[CEGEdge(source="A", target="B")],
        )
        workflow = compiler.compile(graph)
        assert workflow.metadata["has_parallel"] is False

    def test_join_node_receives_all_parallel_outputs(self):
        """The join node D can access outputs from both B and C."""
        compiler = CEGCompiler()
        workflow = compiler.compile(_fan_out_graph())
        result = workflow.invoke()

        # D's input_keys should include A, B, C (since they're all in node_outputs)
        d_output = result["node_outputs"]["D"]
        assert isinstance(d_output, dict)
        # The merge node has access to prior outputs
        assert "input_keys" in d_output


# ══════════════════════════════════════════════════════════════════════
# Fan-in join regression tests
# ══════════════════════════════════════════════════════════════════════


class TestParallelFanInExecutesOnce:
    """Regression tests: a join node must run once, not once per branch.

    The compiler used to derive edges from consecutive pairs of the
    topological order, which chained sibling branches together (B → C)
    on top of the real fan-out. Every node downstream of the fan-out was
    then re-triggered once per branch, silently multiplying cost and
    latency. These tests pin the exactly-once property.
    """

    @staticmethod
    def _counts(result: dict[str, Any]) -> Counter[str]:
        return Counter(entry["node_id"] for entry in result["execution_log"])

    def test_fan_out_every_node_executes_exactly_once(self):
        """Each of A, B, C, D appears exactly once in the execution log."""
        result = CEGCompiler().compile(_fan_out_graph()).invoke()
        assert self._counts(result) == Counter({"A": 1, "B": 1, "C": 1, "D": 1})

    def test_triple_fan_out_every_node_executes_exactly_once(self):
        """A 3-way fan-out still joins into a single execution of E."""
        result = CEGCompiler().compile(_triple_fan_out_graph()).invoke()
        assert self._counts(result) == Counter({"A": 1, "B": 1, "C": 1, "D": 1, "E": 1})

    def test_mixed_sequential_parallel_executes_exactly_once(self):
        """Mixing sequential and parallel edges does not duplicate any node."""
        result = CEGCompiler().compile(_sequential_then_parallel_graph()).invoke()
        assert self._counts(result) == Counter({"A": 1, "B": 1, "C": 1, "D": 1, "E": 1})

    def test_join_cost_is_not_multiplied_by_branch_count(self):
        """Total cost equals the sum of each node's single execution cost."""
        executor = MockExecutor(base_cost=0.01, base_latency_ms=10.0)
        graph = _triple_fan_out_graph()
        result = CEGCompiler(executor=executor).compile(graph).invoke()

        # Each node is billed once, at the price of the model that ran it.
        prices = {m.name: m.estimated_cost for m in DEFAULT_MODEL_REGISTRY}
        log = result["execution_log"]
        assert len(log) == len(graph.nodes)
        expected = sum(prices[entry["model_used"]] for entry in log)
        assert result["total_cost"] == pytest.approx(expected)

    def test_sibling_branches_are_not_chained_together(self):
        """The compiler must not wire an edge between parallel siblings."""
        compiler = CEGCompiler()
        graph = _triple_fan_out_graph()
        wired = set(
            compiler._inject_runtime(
                graph,
                compiler._topological_sort(graph),
                engine=RuntimeDecisionEngine(),
            ).edges
        )

        for left in ("B", "C", "D"):
            for right in ("B", "C", "D"):
                assert (left, right) not in wired

    def test_sequential_join_executes_exactly_once(self):
        """A SEQUENTIAL fan-in joins instead of re-running the downstream path.

        This is the shape the demo pipelines use, and the one the old
        topological-pair wiring broke: it chained B → C → D and then ran
        E once per branch.
        """
        result = CEGCompiler().compile(_sequential_join_graph()).invoke()
        assert self._counts(result) == Counter({"A": 1, "B": 1, "C": 1, "D": 1, "E": 1})

    def test_sequential_join_does_not_chain_siblings(self):
        """No edge is wired between the three sibling branches."""
        compiler = CEGCompiler()
        graph = _sequential_join_graph()
        wired = set(
            compiler._inject_runtime(
                graph,
                compiler._topological_sort(graph),
                engine=RuntimeDecisionEngine(),
            ).edges
        )
        siblings = {"B", "C", "D"}
        assert not [(a, b) for (a, b) in wired if a in siblings and b in siblings]

    def test_parallel_demo_pipeline_executes_each_node_once(self):
        """The shipped parallel demo runs 7 nodes in 7 steps, not 16."""
        from ceg.use_cases.demo_pipelines import (
            ParallelSalesExecutor,
            build_parallel_sales_graph,
        )

        graph = build_parallel_sales_graph()
        result = CEGCompiler(executor=ParallelSalesExecutor()).compile(graph).invoke()

        counts = self._counts(result)
        assert set(counts) == {n.id for n in graph.nodes}
        assert all(c == 1 for c in counts.values()), counts

    def test_parallel_demo_condition_key_matches_executor_output(self):
        """The conditional edge reads a key the executor actually emits.

        The edge declared ``has_anomalies`` while the executor returned
        ``anomalies_found``, so the alert branch was always skipped.
        """
        from ceg.use_cases.demo_pipelines import (
            ParallelSalesExecutor,
            build_parallel_sales_graph,
        )

        graph = build_parallel_sales_graph()
        conditional = [e for e in graph.edges if e.edge_type == EdgeType.CONDITIONAL]
        assert len(conditional) == 1

        detect_output = (
            ParallelSalesExecutor()
            .execute(conditional[0].source, "objective", {})
            .output
        )
        assert conditional[0].condition in detect_output

        result = CEGCompiler(executor=ParallelSalesExecutor()).compile(graph).invoke()
        assert result["node_statuses"][conditional[0].target] == "completed"

    def test_join_node_waits_for_every_branch(self):
        """The join node sees all three branch outputs on its single run."""
        result = CEGCompiler().compile(_triple_fan_out_graph()).invoke()
        e_entry = next(e for e in result["execution_log"] if e["node_id"] == "E")
        assert {"B", "C", "D"}.issubset(set(e_entry["output"]["input_keys"]))
