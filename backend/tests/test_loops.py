"""Tests for CEG Compiler — LOOP edge support (controlled cycles).

Validates that the compiler correctly translates LOOP edges into
conditional back-edges in LangGraph, with iteration counting and
max_iterations guards against infinite loops.
"""

from collections import Counter

import pytest

from ceg.compiler.compiler import CEGCompiler
from ceg.compiler.mock_executor import MockExecutor
from ceg.models.graph import CEGEdge, CEGGraph, EdgeType
from ceg.models.node import CEGNode


# ── Helpers ──────────────────────────────────────────────────────────


class LoopAwareMockExecutor(MockExecutor):
    """MockExecutor that produces outputs with loop-control keys.

    Simulates a node that says "should_loop = True" for a configurable
    number of calls, then "should_loop = False" to exit the loop.
    """

    def __init__(
        self,
        loop_node_id: str,
        loop_true_count: int = 2,
        condition_key: str = "should_loop",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._loop_node_id = loop_node_id
        self._loop_true_count = loop_true_count
        self._condition_key = condition_key
        self._call_count: dict[str, int] = {}

    def execute(self, node_id, objective, inputs, attempt=1):
        self._call_count[node_id] = self._call_count.get(node_id, 0) + 1
        result = super().execute(node_id, objective, inputs, attempt)

        # For the loop-control node, add the condition key
        if node_id == self._loop_node_id:
            output = dict(result.output)
            count = self._call_count[node_id]
            output[self._condition_key] = count <= self._loop_true_count
            from ceg.compiler.mock_executor import ExecutionResult

            return ExecutionResult(
                output=output,
                cost=result.cost,
                latency_ms=result.latency_ms,
                confidence=result.confidence,
            )
        return result


def _simple_loop_graph(max_iterations: int = 5) -> CEGGraph:
    """Build a simple loop: A → B → C →[loop if condition]→ B.

    A ──→ B ──→ C ──[should_loop?]──→ B (loop back)
                  └──[no]──→ END
    """
    return CEGGraph(
        nodes=[
            CEGNode(id="A", objective="Init"),
            CEGNode(id="B", objective="Process", dependencies=["A"]),
            CEGNode(id="C", objective="Evaluate", dependencies=["B"]),
        ],
        edges=[
            CEGEdge(source="A", target="B", edge_type=EdgeType.SEQUENTIAL),
            CEGEdge(source="B", target="C", edge_type=EdgeType.SEQUENTIAL),
            CEGEdge(
                source="C",
                target="B",
                edge_type=EdgeType.LOOP,
                condition="should_loop",
                loop_max_iterations=max_iterations,
            ),
        ],
    )


def _two_node_loop_graph(max_iterations: int = 3) -> CEGGraph:
    """Build a 2-node loop: A →[loop if condition]→ A.

    Actually: A ──→ B ──[should_loop?]──→ A
    """
    return CEGGraph(
        nodes=[
            CEGNode(id="A", objective="Step 1"),
            CEGNode(id="B", objective="Evaluate", dependencies=["A"]),
        ],
        edges=[
            CEGEdge(source="A", target="B", edge_type=EdgeType.SEQUENTIAL),
            CEGEdge(
                source="B",
                target="A",
                edge_type=EdgeType.LOOP,
                condition="should_loop",
                loop_max_iterations=max_iterations,
            ),
        ],
    )


# ══════════════════════════════════════════════════════════════════════
# Model validation tests
# ══════════════════════════════════════════════════════════════════════


class TestLoopModelValidation:
    """Tests that LOOP edges are validated correctly at the model level."""

    def test_loop_edge_requires_max_iterations(self):
        """A LOOP edge without max_iterations raises ValidationError."""
        with pytest.raises(ValueError, match="loop_max_iterations"):
            CEGGraph(
                nodes=[
                    CEGNode(id="A", objective="A"),
                    CEGNode(id="B", objective="B", dependencies=["A"]),
                ],
                edges=[
                    CEGEdge(source="A", target="B", edge_type=EdgeType.SEQUENTIAL),
                    CEGEdge(
                        source="B",
                        target="A",
                        edge_type=EdgeType.LOOP,
                        condition="cond",
                        # loop_max_iterations intentionally missing
                    ),
                ],
            )

    def test_loop_edge_with_max_iterations_is_valid(self):
        """A LOOP edge with max_iterations passes model validation."""
        graph = _simple_loop_graph(max_iterations=3)
        assert len(graph.edges) == 3
        loop_edge = [e for e in graph.edges if e.edge_type == EdgeType.LOOP][0]
        assert loop_edge.loop_max_iterations == 3

    def test_loop_does_not_trigger_cycle_detection(self):
        """A LOOP back-edge does not trigger the DAG cycle validator."""
        # This would raise ValueError if LOOP edges were included in cycle detection
        graph = _two_node_loop_graph(max_iterations=2)
        assert len(graph.nodes) == 2


# ══════════════════════════════════════════════════════════════════════
# Compiler validation tests
# ══════════════════════════════════════════════════════════════════════


class TestLoopCompilerValidation:
    """Tests that the compiler validates LOOP edges correctly."""

    def test_compiler_accepts_loop_edge(self):
        """The compiler accepts LOOP edges."""
        compiler = CEGCompiler()
        graph = _simple_loop_graph()
        compiler._validate(graph)

    def test_topological_sort_excludes_loop_edges(self):
        """The topological sort excludes LOOP back-edges."""
        compiler = CEGCompiler()
        graph = _simple_loop_graph()
        order = compiler._topological_sort(graph)
        # Should produce A, B, C in order (LOOP C→B excluded)
        assert order == ["A", "B", "C"]


# ══════════════════════════════════════════════════════════════════════
# End-to-end execution tests
# ══════════════════════════════════════════════════════════════════════


class TestLoopExecution:
    """Tests that LOOP edges execute correctly with iteration control."""

    def test_loop_executes_correct_iterations(self):
        """Loop executes the expected number of iterations based on condition."""
        # Executor says should_loop=True for 2 calls, then False
        executor = LoopAwareMockExecutor(
            loop_node_id="C", loop_true_count=2
        )
        compiler = CEGCompiler(executor=executor)
        graph = _simple_loop_graph(max_iterations=5)
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        # All nodes should have completed
        assert result["node_statuses"]["A"] == "completed"
        assert result["node_statuses"]["B"] == "completed"
        assert result["node_statuses"]["C"] == "completed"

        # Execution log should show multiple executions of B and C
        b_entries = [e for e in result["execution_log"] if e["node_id"] == "B"]
        c_entries = [e for e in result["execution_log"] if e["node_id"] == "C"]

        # B and C should each execute 3 times: initial + 2 loop iterations
        assert len(b_entries) == 3, f"Expected 3 B entries, got {len(b_entries)}"
        assert len(c_entries) == 3, f"Expected 3 C entries, got {len(c_entries)}"

    def test_loop_respects_max_iterations(self):
        """Loop stops at max_iterations even if condition is still True."""
        # Executor always says should_loop=True (infinite loop)
        executor = LoopAwareMockExecutor(
            loop_node_id="C", loop_true_count=100  # always True
        )
        compiler = CEGCompiler(executor=executor)
        graph = _simple_loop_graph(max_iterations=3)
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        # Should not loop more than max_iterations
        c_entries = [e for e in result["execution_log"] if e["node_id"] == "C"]
        # At most 4 executions: 1 initial + 3 iterations
        assert len(c_entries) <= 4

    def test_loop_accumulates_cost(self):
        """Cost is accumulated across all loop iterations."""
        executor = LoopAwareMockExecutor(
            loop_node_id="C", loop_true_count=1
        )
        compiler = CEGCompiler(executor=executor)
        graph = _simple_loop_graph(max_iterations=5)
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        assert result["total_cost"] > 0.0

    def test_loop_with_no_iterations(self):
        """Loop with condition immediately False executes body only once."""
        executor = LoopAwareMockExecutor(
            loop_node_id="C", loop_true_count=0  # immediately False
        )
        compiler = CEGCompiler(executor=executor)
        graph = _simple_loop_graph(max_iterations=5)
        workflow = compiler.compile(graph)
        result = workflow.invoke()

        c_entries = [e for e in result["execution_log"] if e["node_id"] == "C"]
        assert len(c_entries) == 1  # only the initial execution

    def test_metadata_has_loop_flag(self):
        """Compilation metadata indicates loop edges are present."""
        compiler = CEGCompiler()
        graph = _simple_loop_graph()
        workflow = compiler.compile(graph)
        assert workflow.metadata["has_loops"] is True

    def test_metadata_no_loop_for_linear(self):
        """Linear graph metadata correctly indicates no loops."""
        compiler = CEGCompiler()
        graph = CEGGraph(
            nodes=[
                CEGNode(id="A", objective="A"),
                CEGNode(id="B", objective="B", dependencies=["A"]),
            ],
            edges=[CEGEdge(source="A", target="B")],
        )
        workflow = compiler.compile(graph)
        assert workflow.metadata["has_loops"] is False


# ══════════════════════════════════════════════════════════════════════
# Loop routing regression tests
# ══════════════════════════════════════════════════════════════════════


class _FixedOutputExecutor(MockExecutor):
    """Executor returning the same payload for every node."""

    def __init__(self, payload: dict) -> None:
        super().__init__()
        self.payload = payload

    def execute(self, node_id, objective, inputs, attempt=1):
        from ceg.compiler.mock_executor import ExecutionResult

        return ExecutionResult(
            output=dict(self.payload), cost=0.001, latency_ms=5.0, confidence=1.0
        )


def _counts(graph: CEGGraph, payload: dict) -> Counter:
    """Run ``graph`` and count how many times each node executed."""
    workflow = CEGCompiler(executor=_FixedOutputExecutor(payload)).compile(graph)
    result = workflow.invoke()
    return Counter(entry["node_id"] for entry in result["execution_log"])


def _diamond_loop_graph() -> CEGGraph:
    """A loop whose exit is *not* the next node in topological order.

    A fans out to B and M; both converge on Z; B carries the back-edge.
    Kahn's algorithm orders this [A, B, M, Z], so the node following the
    loop source is M — an unrelated sibling, not B's declared successor.
    """
    return CEGGraph(
        nodes=[
            CEGNode(id="A", objective="A"),
            CEGNode(id="B", objective="B", dependencies=["A"]),
            CEGNode(id="M", objective="M", dependencies=["A"]),
            CEGNode(id="Z", objective="Z", dependencies=["B", "M"]),
        ],
        edges=[
            CEGEdge(source="A", target="B", edge_type=EdgeType.SEQUENTIAL),
            CEGEdge(source="A", target="M", edge_type=EdgeType.SEQUENTIAL),
            CEGEdge(source="M", target="Z", edge_type=EdgeType.SEQUENTIAL),
            CEGEdge(source="B", target="Z", edge_type=EdgeType.SEQUENTIAL),
            CEGEdge(
                source="B",
                target="A",
                edge_type=EdgeType.LOOP,
                condition="should_loop",
                loop_max_iterations=2,
            ),
        ],
    )


class TestLoopExitTarget:
    """The loop exit comes from the declared graph, not the topological order.

    ``continue_node`` used to be ``order[index(source) + 1]``. A sibling
    branch can occupy that slot, which sent the loop out through an
    unrelated node and left the real successor unwired — re-running the
    whole tail of the graph.
    """

    def test_topological_neighbour_is_not_the_declared_successor(self):
        """Guard the premise: the two differ on this graph."""
        compiler = CEGCompiler()
        order = compiler._topological_sort(_diamond_loop_graph())
        assert order[order.index("B") + 1] == "M"  # sibling, not B's successor

    def test_loop_exits_through_its_declared_successor(self):
        """No node runs twice when the loop falls through."""
        assert _counts(_diamond_loop_graph(), {"should_loop": False}) == Counter(
            {"A": 1, "B": 1, "M": 1, "Z": 1}
        )

    def test_loop_source_reaches_its_successor(self):
        """Z is reached even though B's plain edge is owned by the router."""
        graph = _diamond_loop_graph()
        workflow = CEGCompiler(executor=_FixedOutputExecutor({"should_loop": False}))
        result = workflow.compile(graph).invoke()
        assert result["node_statuses"]["Z"] == "completed"


class TestMultipleLoopEdgesPerNode:
    """A node may carry more than one back-edge.

    One ``add_conditional_edges`` call per loop edge raised
    "Branch with name `_loop_router` already exists".
    """

    @staticmethod
    def _two_back_edges_graph() -> CEGGraph:
        return CEGGraph(
            nodes=[CEGNode(id=n, objective=n) for n in ("A", "B", "C")],
            edges=[
                CEGEdge(source="A", target="B", edge_type=EdgeType.SEQUENTIAL),
                CEGEdge(source="B", target="C", edge_type=EdgeType.SEQUENTIAL),
                CEGEdge(
                    source="C",
                    target="A",
                    edge_type=EdgeType.LOOP,
                    condition="r1",
                    loop_max_iterations=1,
                ),
                CEGEdge(
                    source="C",
                    target="B",
                    edge_type=EdgeType.LOOP,
                    condition="r2",
                    loop_max_iterations=1,
                ),
            ],
        )

    def test_two_back_edges_compile(self):
        CEGCompiler().compile(self._two_back_edges_graph())

    def test_no_back_edge_taken_runs_each_node_once(self):
        assert _counts(
            self._two_back_edges_graph(), {"r1": False, "r2": False}
        ) == Counter({"A": 1, "B": 1, "C": 1})

    def test_second_back_edge_loops_to_its_own_target(self):
        """r2 loops back to B, so A is untouched."""
        assert _counts(
            self._two_back_edges_graph(), {"r1": False, "r2": True}
        ) == Counter({"A": 1, "B": 2, "C": 2})

    def test_first_matching_back_edge_wins(self):
        """r1 is declared first, so it takes precedence over r2."""
        assert _counts(
            self._two_back_edges_graph(), {"r1": True, "r2": True}
        ) == Counter({"A": 2, "B": 2, "C": 2})


class TestLoopSourceThatIsAlsoConditional:
    """A node carrying both a back-edge and a guarded forward edge.

    The two routers coexist: the loop decides whether to go back, the
    conditional router owns the forward path.
    """

    @staticmethod
    def _graph() -> CEGGraph:
        return CEGGraph(
            nodes=[CEGNode(id=n, objective=n) for n in ("a", "b", "c")],
            edges=[
                CEGEdge(source="a", target="b", edge_type=EdgeType.SEQUENTIAL),
                CEGEdge(
                    source="b",
                    target="a",
                    edge_type=EdgeType.LOOP,
                    condition="again",
                    loop_max_iterations=2,
                ),
                CEGEdge(
                    source="b",
                    target="c",
                    edge_type=EdgeType.CONDITIONAL,
                    condition="cond",
                ),
            ],
        )

    def test_compiles(self):
        CEGCompiler().compile(self._graph())

    def test_no_loop_and_condition_met(self):
        assert _counts(self._graph(), {"again": False, "cond": True}) == Counter(
            {"a": 1, "b": 1, "c": 1}
        )

    def test_no_loop_and_condition_unmet_skips_the_branch(self):
        workflow = CEGCompiler(
            executor=_FixedOutputExecutor({"again": False, "cond": False})
        ).compile(self._graph())
        result = workflow.invoke()
        assert result["node_statuses"]["c"] == "skipped"

    def test_looping_respects_max_iterations(self):
        """max_iterations=2 allows the initial pass plus two more.

        The guarded forward branch fires once per pass, so c runs on every
        iteration rather than only on loop exit.
        """
        assert _counts(self._graph(), {"again": True, "cond": True}) == Counter(
            {"a": 3, "b": 3, "c": 3}
        )
