"""Tests for subgraphs (CEGGraph nested in a CEGNode) and the hierarchical team."""

from langgraph.checkpoint.memory import MemorySaver

from ceg.compiler.compiler import CEGCompiler
from ceg.compiler.mock_executor import MockExecutor
from ceg.models.graph import CEGEdge, CEGGraph, EdgeType
from ceg.models.node import CEGNode
from ceg.use_cases.multi_agent_supervisor import (
    HierarchicalSupervisorExecutor,
    build_hierarchical_supervisor_graph,
)


class TestSubgraphModels:
    """Test CEGNode subgraph field and properties."""

    def test_node_is_subgraph_false_by_default(self):
        node = CEGNode(id="n1", objective="Simple Node")
        assert not node.is_subgraph
        assert node.subgraph is None

    def test_node_is_subgraph_true_when_graph_provided(self):
        sub = CEGGraph(
            nodes=[CEGNode(id="sub_n1", objective="Inner Node")],
            edges=[],
        )
        node = CEGNode(id="composite_node", objective="Composite", subgraph=sub)
        assert node.is_subgraph
        assert node.subgraph is not None
        assert len(node.subgraph.nodes) == 1

    def test_nested_subgraph_serialization(self):
        sub = CEGGraph(
            nodes=[CEGNode(id="inner", objective="Inner")],
            edges=[],
        )
        parent = CEGGraph(
            nodes=[CEGNode(id="parent", objective="Parent", subgraph=sub)],
            edges=[],
        )
        data = parent.model_dump()
        assert data["nodes"][0]["subgraph"]["nodes"][0]["id"] == "inner"


class TestSubgraphCompilationAndExecution:
    """Test compiling and executing workflows with nested subgraphs."""

    def test_compile_simple_subgraph_metadata(self):
        sub = CEGGraph(
            nodes=[
                CEGNode(id="sub1", objective="Step 1"),
                CEGNode(id="sub2", objective="Step 2", dependencies=["sub1"]),
            ],
            edges=[
                CEGEdge(source="sub1", target="sub2", edge_type=EdgeType.SEQUENTIAL)
            ],
        )
        parent = CEGGraph(
            nodes=[
                CEGNode(id="start", objective="Start"),
                CEGNode(
                    id="process_sub",
                    objective="Process in subgraph",
                    dependencies=["start"],
                    subgraph=sub,
                ),
                CEGNode(id="finish", objective="Finish", dependencies=["process_sub"]),
            ],
            edges=[
                CEGEdge(
                    source="start", target="process_sub", edge_type=EdgeType.SEQUENTIAL
                ),
                CEGEdge(
                    source="process_sub", target="finish", edge_type=EdgeType.SEQUENTIAL
                ),
            ],
        )

        compiler = CEGCompiler()
        workflow = compiler.compile(parent)
        assert workflow.metadata["has_subgraphs"] is True
        assert workflow.metadata["node_count"] == 3

    def test_execute_subgraph_passes_and_aggregates_state(self):
        sub = CEGGraph(
            nodes=[
                CEGNode(id="sub1", objective="Step 1"),
                CEGNode(id="sub2", objective="Step 2", dependencies=["sub1"]),
            ],
            edges=[
                CEGEdge(source="sub1", target="sub2", edge_type=EdgeType.SEQUENTIAL)
            ],
        )
        parent = CEGGraph(
            nodes=[
                CEGNode(id="start", objective="Start"),
                CEGNode(
                    id="team_work",
                    objective="Team execution",
                    dependencies=["start"],
                    subgraph=sub,
                ),
                CEGNode(id="summary", objective="Summary", dependencies=["team_work"]),
            ],
            edges=[
                CEGEdge(
                    source="start", target="team_work", edge_type=EdgeType.SEQUENTIAL
                ),
                CEGEdge(
                    source="team_work", target="summary", edge_type=EdgeType.SEQUENTIAL
                ),
            ],
        )

        compiler = CEGCompiler()
        wf = compiler.compile(parent)
        state = wf.invoke({"init_val": "hello"})

        # Check all parent and sub nodes completed
        assert "start" in state["node_outputs"]
        assert "team_work" in state["node_outputs"]
        assert "summary" in state["node_outputs"]

        # Inner outputs are encapsulated under parent composite node
        team_out = state["node_outputs"]["team_work"]
        assert "sub1" in team_out
        assert "sub2" in team_out

        # Check execution log contains inner step records tagged with subgraph_parent
        log_sub = [
            e for e in state["execution_log"] if e.get("subgraph_parent") == "team_work"
        ]
        assert len(log_sub) >= 2


class TestHierarchicalMultiAgentSupervisor:
    """Hierarchical multi-agent supervisor with research and analytics teams."""

    def test_compile_hierarchical_supervisor_graph(self):
        graph = build_hierarchical_supervisor_graph()
        compiler = CEGCompiler()
        workflow = compiler.compile(graph)

        assert workflow.metadata["has_subgraphs"] is True
        assert workflow.metadata["node_count"] == 4

    def test_execute_hierarchical_supervisor_e2e(self):
        graph = build_hierarchical_supervisor_graph()
        executor = HierarchicalSupervisorExecutor()
        compiler = CEGCompiler(executor=executor)
        workflow = compiler.compile(graph)

        state = workflow.invoke({"query": "Analyse du marché GenAI Q1"})

        # Top-level nodes completed
        assert state["node_statuses"]["supervisor_dispatch"] == "completed"
        assert state["node_statuses"]["research_team"] == "completed"
        assert state["node_statuses"]["analytics_team"] == "completed"
        assert state["node_statuses"]["executive_summary"] == "completed"

        # Check research team output
        research_outputs = state["node_outputs"]["research_team"]
        assert "web_search_agent" in research_outputs
        assert "db_extractor_agent" in research_outputs
        assert "merge_research" in research_outputs

        # Check analytics team output
        analytics_outputs = state["node_outputs"]["analytics_team"]
        assert "model_analysis" in analytics_outputs
        assert "quality_audit" in analytics_outputs
        assert "formatted_insights" in analytics_outputs

        # Check executive summary
        summary_out = state["node_outputs"]["executive_summary"]
        assert "Briefing Exécutif" in summary_out["summary"]

        # Verify cost and latency accumulated properly
        assert state["total_cost"] > 0
        assert state["total_latency_ms"] > 0


class TestSubgraphWithCheckpointer:
    """Regression tests: compiling a subgraph node with a checkpointer.

    ``_make_runtime_node_function`` referenced a ``checkpointer`` name that
    was never passed in nor captured, so any graph combining a subgraph
    node with a checkpointer raised ``NameError`` at execution time.
    """

    def test_subgraph_executes_with_checkpointer(self):
        workflow = CEGCompiler(executor=HierarchicalSupervisorExecutor()).compile(
            build_hierarchical_supervisor_graph(),
            checkpointer=MemorySaver(),
        )
        state = workflow.invoke(thread_id="thread-subgraph")

        assert state["node_statuses"]["research_team"] == "completed"
        assert state["node_statuses"]["analytics_team"] == "completed"
        assert state["node_statuses"]["executive_summary"] == "completed"

    def test_inner_subgraph_hitl_node_interrupts(self):
        """A checkpointer reaches the inner graph, so nested HITL still pauses."""
        inner = CEGGraph(
            nodes=[
                CEGNode(id="inner_a", objective="Inner step"),
                CEGNode(
                    id="inner_b",
                    objective="Inner approval",
                    dependencies=["inner_a"],
                    interrupt_before=True,
                ),
            ],
            edges=[CEGEdge(source="inner_a", target="inner_b")],
        )
        outer = CEGGraph(
            nodes=[CEGNode(id="team", objective="Composite team", subgraph=inner)],
            edges=[],
        )

        workflow = CEGCompiler(executor=MockExecutor()).compile(
            outer, checkpointer=MemorySaver()
        )
        state = workflow.invoke(thread_id="thread-nested-hitl")

        assert "__interrupt__" in state
        interrupt = state["__interrupt__"][0]
        assert interrupt.value["node_id"] == "inner_b"
        assert interrupt.value["action"] == "approve_before"
