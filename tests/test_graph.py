"""Tests for CEGGraph model and DAG cycle validation."""

import pytest
from pydantic import ValidationError

from ceg.models.graph import CEGGraph
from ceg.models.node import CEGNode
from ceg.models.task import CognitiveTask


def test_valid_dag_creation() -> None:
    """Test creation of a valid DAG graph."""
    node_a = CEGNode(id="A", objective="Root task")
    node_b = CEGNode(id="B", objective="Intermediate task", dependencies=["A"])
    node_c = CEGNode(id="C", objective="Final task", dependencies=["B"])

    graph = CEGGraph(nodes=[node_a, node_b, node_c], edges=[("A", "B"), ("B", "C")])
    assert len(graph.nodes) == 3
    assert len(graph.edges) == 2


def test_diamond_dag_creation() -> None:
    """Test creation of a valid diamond-shaped DAG graph."""
    node_a = CEGNode(id="A", objective="Start")
    node_b = CEGNode(id="B", objective="Left branch", dependencies=["A"])
    node_c = CEGNode(id="C", objective="Right branch", dependencies=["A"])
    node_d = CEGNode(id="D", objective="Merge", dependencies=["B", "C"])

    graph = CEGGraph(nodes=[node_a, node_b, node_c, node_d])
    assert len(graph.nodes) == 4


def test_reject_duplicate_node_ids() -> None:
    """Test that graph rejects duplicate node IDs."""
    node_1 = CEGNode(id="A", objective="First A")
    node_2 = CEGNode(id="A", objective="Second A")

    with pytest.raises(ValidationError) as exc_info:
        CEGGraph(nodes=[node_1, node_2])
    assert "Duplicate node IDs found" in str(exc_info.value)


def test_reject_self_loop_cycle() -> None:
    """Test that graph rejects self-referencing cycle A -> A."""
    node_a = CEGNode(id="A", objective="Self loop", dependencies=["A"])

    with pytest.raises(ValidationError) as exc_info:
        CEGGraph(nodes=[node_a])
    assert "Graph contains at least one cycle" in str(exc_info.value)


def test_reject_two_node_cycle() -> None:
    """Test that graph rejects 2-node cycle A -> B -> A."""
    node_a = CEGNode(id="A", objective="Node A", dependencies=["B"])
    node_b = CEGNode(id="B", objective="Node B", dependencies=["A"])

    with pytest.raises(ValidationError) as exc_info:
        CEGGraph(nodes=[node_a, node_b])
    assert "Graph contains at least one cycle" in str(exc_info.value)


def test_reject_three_node_cycle_via_edges() -> None:
    """Test that graph rejects 3-node cycle A -> B -> C -> A via explicit edges."""
    node_a = CEGNode(id="A", objective="Node A")
    node_b = CEGNode(id="B", objective="Node B")
    node_c = CEGNode(id="C", objective="Node C")

    with pytest.raises(ValidationError) as exc_info:
        CEGGraph(
            nodes=[node_a, node_b, node_c],
            edges=[("A", "B"), ("B", "C"), ("C", "A")],
        )
    assert "Graph contains at least one cycle" in str(exc_info.value)


def test_reject_non_existent_dependency() -> None:
    """Test that referencing a missing dependency node raises ValidationError."""
    node_a = CEGNode(id="A", objective="Node A", dependencies=["UNKNOWN"])

    with pytest.raises(ValidationError) as exc_info:
        CEGGraph(nodes=[node_a])
    assert "references non-existent dependency node 'UNKNOWN'" in str(exc_info.value)


def test_reject_non_existent_edge_target() -> None:
    """Test that explicit edge targeting non-existent node raises ValidationError."""
    node_a = CEGNode(id="A", objective="Node A")

    with pytest.raises(ValidationError) as exc_info:
        CEGGraph(nodes=[node_a], edges=[("A", "MISSING")])
    assert "Edge references non-existent target node 'MISSING'" in str(exc_info.value)


def test_reject_non_existent_edge_source() -> None:
    """Test explicit edge from missing source node raises ValidationError."""
    node_a = CEGNode(id="A", objective="Node A")

    with pytest.raises(ValidationError) as exc_info:
        CEGGraph(nodes=[node_a], edges=[("MISSING", "A")])
    assert "Edge references non-existent source node 'MISSING'" in str(exc_info.value)


def test_graph_json_serialization() -> None:
    """Test JSON serialization and deserialization of complete CEGGraph."""
    task = CognitiveTask(
        objective="Root objective",
        constraints=["Fast execution"],
    )
    node_a = CEGNode(id="A", objective="Start node", task=task)
    node_b = CEGNode(id="B", objective="End node", dependencies=["A"])

    graph = CEGGraph(nodes=[node_a, node_b], edges=[("A", "B")])

    json_str = graph.model_dump_json()
    reconstructed = CEGGraph.model_validate_json(json_str)

    assert reconstructed == graph
    assert len(reconstructed.nodes) == 2
    assert reconstructed.nodes[0].task is not None
    assert reconstructed.nodes[0].task.objective == "Root objective"
