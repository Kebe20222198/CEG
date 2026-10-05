"""Tests for CognitiveTask model."""

import pytest
from pydantic import ValidationError

from ceg.models.task import CognitiveTask


def test_cognitive_task_creation_valid() -> None:
    """Test valid creation of a CognitiveTask."""
    task = CognitiveTask(
        objective="Summarize technical document",
        constraints=["Max 500 words", "Use French language"],
        success_criteria=["Accurate summary", "Key points included"],
        metadata={"domain": "AI", "priority": "high"},
    )
    assert task.objective == "Summarize technical document"
    assert len(task.constraints) == 2
    assert len(task.success_criteria) == 2
    assert task.metadata["domain"] == "AI"


def test_cognitive_task_defaults() -> None:
    """Test CognitiveTask default fields."""
    task = CognitiveTask(objective="Minimal task")
    assert task.objective == "Minimal task"
    assert task.constraints == []
    assert task.success_criteria == []
    assert task.metadata == {}


def test_cognitive_task_validation_missing_objective() -> None:
    """Test missing required objective field raises ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        CognitiveTask.model_validate({})
    assert "objective" in str(exc_info.value)


def test_cognitive_task_validation_empty_objective() -> None:
    """Test empty string objective raises ValidationError."""
    with pytest.raises(ValidationError):
        CognitiveTask(objective="")


def test_cognitive_task_json_serialization() -> None:
    """Test JSON serialization and deserialization of CognitiveTask."""
    task = CognitiveTask(
        objective="Extract entities",
        constraints=["JSON output only"],
        success_criteria=["Valid JSON schema"],
    )
    json_str = task.model_dump_json()
    reconstructed = CognitiveTask.model_validate_json(json_str)

    assert reconstructed == task
    assert reconstructed.objective == "Extract entities"
