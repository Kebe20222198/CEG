"""Tests for CEG Compiler v1 — validation, sort, translation, end-to-end."""

from typing import Any

import pytest
from pydantic import ValidationError

from ceg.compiler.compiler import CEGCompiler
from ceg.compiler.mock_executor import ExecutionResult, MockExecutor
from ceg.models.graph import CEGEdge, CEGGraph, EdgeType
from ceg.models.node import CEGNode, ModelTierHint
from ceg.models.task import CognitiveTask

# ── helpers ──────────────────────────────────────────────────────────


def _linear_graph(n: int = 3) -> CEGGraph:
    """Build a linear chain A → B → C (... up to n nodes)."""
    labels = [chr(ord("A") + i) for i in range(n)]
    nodes = [
        CEGNode(
            id=labels[i],
            objective=f"Task {labels[i]}",
            dependencies=[labels[i - 1]] if i > 0 else [],
        )
        for i in range(n)
    ]
    edges = [CEGEdge(source=labels[i], target=labels[i + 1]) for i in range(n - 1)]
    return CEGGraph(nodes=nodes, edges=edges)


def _diamond_graph() -> CEGGraph:
    """Build a diamond DAG: A → {B, C} → D."""
    return CEGGraph(
        nodes=[
            CEGNode(id="A", objective="Root"),
            CEGNode(id="B", objective="Left", dependencies=["A"]),
            CEGNode(id="C", objective="Right", dependencies=["A"]),
            CEGNode(id="D", objective="Merge", dependencies=["B", "C"]),
        ],
        edges=[
            CEGEdge(source="A", target="B"),
            CEGEdge(source="A", target="C"),
            CEGEdge(source="B", target="D"),
            CEGEdge(source="C", target="D"),
        ],
    )


def _sales_pipeline_graph() -> CEGGraph:
    """Build the 4-node sales anomaly detection pipeline."""
    return CEGGraph(
        nodes=[
            CEGNode(
                id="fetch_data",
                objective="Fetch raw sales data from regional databases",
                task=CognitiveTask(objective="Retrieve daily sales records"),
                required_capabilities=["data_retrieval"],
            ),
            CEGNode(
                id="aggregate",
                objective="Aggregate sales metrics by region and product category",
                dependencies=["fetch_data"],
                task=CognitiveTask(objective="Compute totals and averages"),
                required_capabilities=["data_analysis"],
            ),
            CEGNode(
                id="detect_anomaly",
                objective="Detect statistical anomalies in aggregated sales data",
                dependencies=["aggregate"],
                task=CognitiveTask(objective="Identify outliers"),
                required_capabilities=["anomaly_detection"],
                model_tier_hint=ModelTierHint.QUALITY,
            ),
            CEGNode(
                id="generate_alert",
                objective="Generate alert report for detected anomalies",
                dependencies=["detect_anomaly"],
                task=CognitiveTask(objective="Produce alert summary"),
                required_capabilities=["text_generation"],
                model_tier_hint=ModelTierHint.BALANCED,
            ),
        ],
        edges=[
            CEGEdge(source="fetch_data", target="aggregate"),
            CEGEdge(source="aggregate", target="detect_anomaly"),
            CEGEdge(source="detect_anomaly", target="generate_alert"),
        ],
    )


# ══════════════════════════════════════════════════════════════════════
# MockExecutor tests
# ══════════════════════════════════════════════════════════════════════


class TestMockExecutor:
    """Tests for the mock executor."""

    def test_returns_valid_result(self):
        """MockExecutor returns an ExecutionResult with all required fields."""
        executor = MockExecutor()
        result = executor.execute(
            node_id="test_node",
            objective="Analyze something",
            inputs={"upstream": "data"},
        )

        assert isinstance(result, ExecutionResult)
        assert result.cost > 0
        assert result.latency_ms > 0
        assert 0.0 <= result.confidence <= 1.0
        assert result.output is not None
        assert result.output["node_id"] == "test_node"

    def test_deterministic_output(self):
        """Same inputs produce identical outputs across calls."""
        executor = MockExecutor()
        r1 = executor.execute("n1", "Do X", {"a": 1})
        r2 = executor.execute("n1", "Do X", {"a": 1})

        assert r1 == r2

    def test_cost_scales_with_objective_length(self):
        """Cost is proportional to objective string length."""
        executor = MockExecutor(base_cost=0.01)
        short = executor.execute("n", "Hi", {})
        long = executor.execute("n", "A much longer objective string", {})

        assert long.cost > short.cost

    def test_latency_scales_with_node_id_length(self):
        """Latency increases with node_id length."""
        executor = MockExecutor()
        short = executor.execute("a", "obj", {})
        long = executor.execute("a_very_long_node_id", "obj", {})

        assert long.latency_ms > short.latency_ms


# ══════════════════════════════════════════════════════════════════════
# Validation tests
# ══════════════════════════════════════════════════════════════════════


class TestValidation:
    """Tests for CEGCompiler._validate."""

    def test_validate_valid_acyclic_graph(self):
        """A valid acyclic graph passes validation without errors."""
        compiler = CEGCompiler()
        graph = _linear_graph(4)
        # Should not raise
        compiler._validate(graph)

    def test_validate_rejects_cycle(self):
        """Creating a graph with a cycle raises ValidationError at model level."""
        with pytest.raises(ValidationError, match="cycle"):
            CEGGraph(
                nodes=[
                    CEGNode(id="A", objective="A", dependencies=["C"]),
                    CEGNode(id="B", objective="B", dependencies=["A"]),
                    CEGNode(id="C", objective="C", dependencies=["B"]),
                ],
            )

    def test_validate_rejects_empty_graph(self):
        """An empty graph (no nodes) is rejected by the compiler."""
        compiler = CEGCompiler()
        graph = CEGGraph(nodes=[], edges=[])
        with pytest.raises(ValueError, match="at least one node"):
            compiler._validate(graph)

    def test_validate_accepts_conditional_edge(self):
        """A graph with a CONDITIONAL edge is accepted by the S4 compiler."""
        compiler = CEGCompiler()
        graph = CEGGraph(
            nodes=[
                CEGNode(id="A", objective="A"),
                CEGNode(id="B", objective="B"),
            ],
            edges=[
                CEGEdge(
                    source="A",
                    target="B",
                    edge_type=EdgeType.CONDITIONAL,
                    condition="some_flag",
                ),
            ],
        )
        # Should not raise — CONDITIONAL is now supported
        compiler._validate(graph)

    def test_validate_accepts_parallel_edge(self):
        """Parallel edges are accepted by the compiler (fan-out/fan-in)."""
        compiler = CEGCompiler()
        graph = CEGGraph(
            nodes=[
                CEGNode(id="A", objective="A"),
                CEGNode(id="B", objective="B"),
            ],
            edges=[
                CEGEdge(
                    source="A",
                    target="B",
                    edge_type=EdgeType.PARALLEL,
                ),
            ],
        )
        # Should NOT raise — PARALLEL is now a supported edge type
        compiler._validate(graph)

    def test_validate_single_node_graph(self):
        """A single-node graph with no edges is valid."""
        compiler = CEGCompiler()
        graph = CEGGraph(nodes=[CEGNode(id="solo", objective="Solo task")])
        compiler._validate(graph)


# ══════════════════════════════════════════════════════════════════════
# Topological sort tests
# ══════════════════════════════════════════════════════════════════════


class TestTopologicalSort:
    """Tests for CEGCompiler._topological_sort."""

    def test_linear_chain(self):
        """Linear chain A → B → C produces [A, B, C]."""
        compiler = CEGCompiler()
        graph = _linear_graph(3)
        order = compiler._topological_sort(graph)
        assert order == ["A", "B", "C"]

    def test_diamond_graph(self):
        """Diamond graph A → {B, C} → D respects all dependencies."""
        compiler = CEGCompiler()
        graph = _diamond_graph()
        order = compiler._topological_sort(graph)

        assert order[0] == "A"
        assert order[-1] == "D"
        assert set(order) == {"A", "B", "C", "D"}
        # B and C must come after A and before D
        assert order.index("B") > order.index("A")
        assert order.index("C") > order.index("A")
        assert order.index("B") < order.index("D")
        assert order.index("C") < order.index("D")

    def test_single_node(self):
        """Single-node graph returns a list with just that node."""
        compiler = CEGCompiler()
        graph = CEGGraph(nodes=[CEGNode(id="solo", objective="Solo")])
        order = compiler._topological_sort(graph)
        assert order == ["solo"]

    def test_five_node_chain(self):
        """5-node linear chain returns correct order."""
        compiler = CEGCompiler()
        graph = _linear_graph(5)
        order = compiler._topological_sort(graph)
        assert order == ["A", "B", "C", "D", "E"]

    def test_dependencies_without_explicit_edges(self):
        """Topological sort works with dependencies alone (no explicit edges)."""
        compiler = CEGCompiler()
        graph = CEGGraph(
            nodes=[
                CEGNode(id="X", objective="X"),
                CEGNode(id="Y", objective="Y", dependencies=["X"]),
                CEGNode(id="Z", objective="Z", dependencies=["Y"]),
            ],
        )
        order = compiler._topological_sort(graph)
        assert order == ["X", "Y", "Z"]


# ══════════════════════════════════════════════════════════════════════
# CompiledWorkflow tests
# ══════════════════════════════════════════════════════════════════════


class TestCompiledWorkflow:
    """Tests for CompiledWorkflow."""

    def test_invoke_with_defaults(self):
        """CompiledWorkflow.invoke() works with no arguments (default state)."""
        compiler = CEGCompiler()
        graph = _linear_graph(2)
        workflow = compiler.compile(graph)

        result = workflow.invoke()
        assert result is not None
        assert "node_outputs" in result
        assert "node_statuses" in result

    def test_invoke_with_custom_initial_state(self):
        """CompiledWorkflow.invoke() accepts custom initial state overrides."""
        compiler = CEGCompiler()
        graph = CEGGraph(
            nodes=[CEGNode(id="only", objective="Single node")],
        )
        workflow = compiler.compile(graph)

        result = workflow.invoke({"node_outputs": {"prior": "value"}})
        # The prior value should be merged with the node output
        assert "prior" in result["node_outputs"]
        assert "only" in result["node_outputs"]

    def test_metadata_populated(self):
        """CompiledWorkflow metadata contains expected fields."""
        compiler = CEGCompiler()
        graph = _linear_graph(3)
        workflow = compiler.compile(graph)

        assert workflow.metadata["node_count"] == 3
        assert workflow.metadata["edge_count"] == 2
        assert workflow.metadata["execution_order"] == ["A", "B", "C"]
        assert workflow.metadata["runtime_target"] == "langgraph"


# ══════════════════════════════════════════════════════════════════════
# End-to-end execution tests
# ══════════════════════════════════════════════════════════════════════


class TestEndToEnd:
    """Full end-to-end compilation and execution tests."""

    def test_sales_pipeline_all_nodes_completed(self):
        """All 4 nodes in the sales pipeline reach 'completed' status."""
        compiler = CEGCompiler()
        graph = _sales_pipeline_graph()
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        expected_nodes = {"fetch_data", "aggregate", "detect_anomaly", "generate_alert"}
        assert set(result["node_statuses"].keys()) == expected_nodes
        for node_id, status in result["node_statuses"].items():
            assert status == "completed", f"Node '{node_id}' has status '{status}'"

    def test_sales_pipeline_cost_accumulated(self):
        """Total cost is positive and equals the sum of individual node costs."""
        compiler = CEGCompiler()
        graph = _sales_pipeline_graph()
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        total_cost = result["total_cost"]
        assert total_cost > 0

        log_cost_sum = sum(entry["cost"] for entry in result["execution_log"])
        assert abs(total_cost - log_cost_sum) < 1e-6

    def test_sales_pipeline_latency_accumulated(self):
        """Total latency is positive and equals the sum of individual node latencies."""
        compiler = CEGCompiler()
        graph = _sales_pipeline_graph()
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        total_latency = result["total_latency_ms"]
        assert total_latency > 0

        log_latency_sum = sum(entry["latency_ms"] for entry in result["execution_log"])
        assert abs(total_latency - log_latency_sum) < 1e-6

    def test_sales_pipeline_outputs_non_empty(self):
        """Every node produces a non-None output."""
        compiler = CEGCompiler()
        graph = _sales_pipeline_graph()
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        for node_id in ["fetch_data", "aggregate", "detect_anomaly", "generate_alert"]:
            assert node_id in result["node_outputs"]
            assert result["node_outputs"][node_id] is not None

    def test_sales_pipeline_execution_log_order(self):
        """Execution log entries appear in topological order."""
        compiler = CEGCompiler()
        graph = _sales_pipeline_graph()
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        log_node_ids = [entry["node_id"] for entry in result["execution_log"]]
        assert log_node_ids == [
            "fetch_data",
            "aggregate",
            "detect_anomaly",
            "generate_alert",
        ]

    def test_sales_pipeline_confidence_in_range(self):
        """All confidence scores in the execution log are in [0, 1]."""
        compiler = CEGCompiler()
        graph = _sales_pipeline_graph()
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        for entry in result["execution_log"]:
            assert 0.0 <= entry["confidence"] <= 1.0

    def test_single_node_execution(self):
        """A single-node graph compiles and executes successfully."""
        compiler = CEGCompiler()
        graph = CEGGraph(
            nodes=[CEGNode(id="solo", objective="One-shot task")],
        )
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        assert result["node_statuses"] == {"solo": "completed"}
        assert result["total_cost"] > 0
        assert result["total_latency_ms"] > 0
        assert "solo" in result["node_outputs"]

    def test_linear_chain_execution(self):
        """A 5-node linear chain executes all nodes in order."""
        compiler = CEGCompiler()
        graph = _linear_graph(5)
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        assert len(result["node_statuses"]) == 5
        for label in ["A", "B", "C", "D", "E"]:
            assert result["node_statuses"][label] == "completed"

        log_ids = [e["node_id"] for e in result["execution_log"]]
        assert log_ids == ["A", "B", "C", "D", "E"]


# ══════════════════════════════════════════════════════════════════════
# Edge-case and error tests
# ══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge-case and error handling tests."""

    def test_unsupported_runtime_target(self):
        """CEGCompiler rejects unsupported runtime targets."""
        with pytest.raises(ValueError, match="Unsupported runtime target"):
            CEGCompiler(runtime_target="airflow")

    def test_backward_compatible_tuple_edges(self):
        """Tuple-style edges still work for backward compatibility."""
        graph = CEGGraph(
            nodes=[
                CEGNode(id="A", objective="A"),
                CEGNode(id="B", objective="B"),
            ],
            edges=[("A", "B")],  # type: ignore[list-item]
        )
        compiler = CEGCompiler()
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        assert result["node_statuses"]["A"] == "completed"
        assert result["node_statuses"]["B"] == "completed"


# ══════════════════════════════════════════════════════════════════════
# Conditional routing regression tests
# ══════════════════════════════════════════════════════════════════════


class _FixedOutputExecutor(MockExecutor):
    """Executor returning the same payload for every node."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__()
        self.payload = payload

    def run(self, node_id, objective, inputs, attempt=1):
        return ExecutionResult(
            output=dict(self.payload), cost=0.001, latency_ms=10.0, confidence=1.0
        )


def _run(graph: CEGGraph, payload: dict[str, Any]) -> dict[str, Any]:
    executor = _FixedOutputExecutor(payload)
    return CEGCompiler(executor=executor).compile(graph).invoke()


class TestConditionalRouting:
    """Regression tests for conditional edges.

    Two defects are covered here. A node could carry only one conditional
    edge, because the compiler called ``add_conditional_edges`` once per
    edge and LangGraph allows a single branch per node. And an unmet
    condition routed the skip handler straight to ``END``, which silently
    dropped every node downstream of the skipped one.
    """

    def test_unmet_condition_does_not_drop_downstream_nodes(self):
        """b is skipped but c, which comes after b, still executes."""
        graph = CEGGraph(
            nodes=[CEGNode(id=n, objective=n) for n in ("a", "b", "c")],
            edges=[
                CEGEdge(
                    source="a",
                    target="b",
                    edge_type=EdgeType.CONDITIONAL,
                    condition="cond",
                ),
                CEGEdge(source="b", target="c", edge_type=EdgeType.SEQUENTIAL),
            ],
        )
        result = _run(graph, {"cond": False})

        assert result["node_statuses"] == {
            "a": "completed",
            "b": "skipped",
            "c": "completed",
        }

    def test_met_condition_runs_the_whole_chain(self):
        graph = CEGGraph(
            nodes=[CEGNode(id=n, objective=n) for n in ("a", "b", "c")],
            edges=[
                CEGEdge(
                    source="a",
                    target="b",
                    edge_type=EdgeType.CONDITIONAL,
                    condition="cond",
                ),
                CEGEdge(source="b", target="c", edge_type=EdgeType.SEQUENTIAL),
            ],
        )
        result = _run(graph, {"cond": True})

        assert all(s == "completed" for s in result["node_statuses"].values())

    @staticmethod
    def _two_branch_graph() -> CEGGraph:
        return CEGGraph(
            nodes=[CEGNode(id=n, objective=n) for n in ("router", "left", "right")],
            edges=[
                CEGEdge(
                    source="router",
                    target="left",
                    edge_type=EdgeType.CONDITIONAL,
                    condition="go_left",
                ),
                CEGEdge(
                    source="router",
                    target="right",
                    edge_type=EdgeType.CONDITIONAL,
                    condition="go_right",
                ),
            ],
        )

    def test_two_conditional_edges_from_one_node_compile(self):
        """Two guarded successors no longer raise 'Branch already exists'."""
        CEGCompiler().compile(self._two_branch_graph())

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"go_left": True, "go_right": False}, ("completed", "skipped")),
            ({"go_left": False, "go_right": True}, ("skipped", "completed")),
            ({"go_left": True, "go_right": True}, ("completed", "completed")),
            ({"go_left": False, "go_right": False}, ("skipped", "skipped")),
        ],
    )
    def test_each_branch_is_decided_independently(self, payload, expected):
        """Every combination of the two guards routes as declared."""
        result = _run(self._two_branch_graph(), payload)
        left, right = expected
        assert result["node_statuses"]["left"] == left
        assert result["node_statuses"]["right"] == right

    def test_unconditional_successor_of_a_conditional_source_still_runs(self):
        """A plain edge leaving a conditional source is not swallowed."""
        graph = CEGGraph(
            nodes=[CEGNode(id=n, objective=n) for n in ("a", "opt", "always")],
            edges=[
                CEGEdge(
                    source="a",
                    target="opt",
                    edge_type=EdgeType.CONDITIONAL,
                    condition="cond",
                ),
                CEGEdge(source="a", target="always", edge_type=EdgeType.SEQUENTIAL),
            ],
        )
        result = _run(graph, {"cond": False})

        assert result["node_statuses"]["opt"] == "skipped"
        assert result["node_statuses"]["always"] == "completed"

    def test_skipping_a_node_cascades_to_its_guarded_successor(self):
        """c is guarded by b's output, so skipping b skips c rather than
        running it against a missing output."""
        graph = CEGGraph(
            nodes=[
                CEGNode(id="a", objective="a"),
                CEGNode(id="b", objective="b", dependencies=["a"]),
                CEGNode(id="c", objective="c", dependencies=["b"]),
            ],
            edges=[
                CEGEdge(
                    source="a",
                    target="b",
                    edge_type=EdgeType.CONDITIONAL,
                    condition="c1",
                ),
                CEGEdge(
                    source="b",
                    target="c",
                    edge_type=EdgeType.CONDITIONAL,
                    condition="c2",
                ),
            ],
        )
        result = _run(graph, {"c1": False, "c2": True})

        assert result["node_statuses"]["b"] == "skipped"
        assert result["node_statuses"]["c"] == "skipped"

    def test_skipped_node_is_recorded_in_the_execution_log(self):
        """The skip handler still emits a zero-cost log entry."""
        graph = CEGGraph(
            nodes=[CEGNode(id=n, objective=n) for n in ("a", "b", "c")],
            edges=[
                CEGEdge(
                    source="a",
                    target="b",
                    edge_type=EdgeType.CONDITIONAL,
                    condition="cond",
                ),
                CEGEdge(source="b", target="c", edge_type=EdgeType.SEQUENTIAL),
            ],
        )
        result = _run(graph, {"cond": False})

        entry = next(e for e in result["execution_log"] if e["node_id"] == "b")
        assert entry["status"] == "skipped"
        assert entry["cost"] == 0.0
        assert entry["model_used"] is None
