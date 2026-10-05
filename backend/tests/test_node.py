"""Tests for CEGNode model."""

import pytest
from pydantic import ValidationError

from ceg.models.node import CEGNode
from ceg.models.task import CognitiveTask


def test_ceg_node_creation_valid() -> None:
    """Test valid creation of a CEGNode."""
    task = CognitiveTask(objective="Subtask objective")
    node = CEGNode(
        id="node-1",
        objective="Analyze data",
        dependencies=["node-0"],
        accumulated_cost=0.015,
        confidence_score=0.95,
        execution_history=[{"step": 1, "status": "success"}],
        task=task,
    )
    assert node.id == "node-1"
    assert node.objective == "Analyze data"
    assert node.dependencies == ["node-0"]
    assert node.accumulated_cost == 0.015
    assert node.confidence_score == 0.95
    assert len(node.execution_history) == 1
    assert node.task is not None
    assert node.task.objective == "Subtask objective"


def test_ceg_node_defaults() -> None:
    """Test default values of CEGNode."""
    node = CEGNode(id="node-default", objective="Default node test")
    assert node.id == "node-default"
    assert node.dependencies == []
    assert node.accumulated_cost == 0.0
    assert node.confidence_score == 1.0
    assert node.execution_history == []
    assert node.task is None


def test_ceg_node_validation_negative_cost() -> None:
    """Test negative accumulated cost raises ValidationError."""
    with pytest.raises(ValidationError):
        CEGNode(id="node-invalid", objective="Invalid cost", accumulated_cost=-0.01)


def test_ceg_node_validation_confidence_out_of_bounds() -> None:
    """Test confidence_score < 0.0 or > 1.0 raises ValidationError."""
    with pytest.raises(ValidationError):
        CEGNode(id="node-1", objective="Low confidence", confidence_score=-0.1)

    with pytest.raises(ValidationError):
        CEGNode(id="node-2", objective="High confidence", confidence_score=1.1)


def test_ceg_node_json_serialization() -> None:
    """Test JSON serialization and deserialization of CEGNode."""
    task = CognitiveTask(objective="Task inside node")
    node = CEGNode(
        id="node-json",
        objective="JSON serialization node",
        dependencies=["root-1"],
        accumulated_cost=0.042,
        confidence_score=0.88,
        task=task,
    )
    json_str = node.model_dump_json()
    reconstructed = CEGNode.model_validate_json(json_str)

    assert reconstructed == node
    assert reconstructed.id == "node-json"
    assert reconstructed.task == task
