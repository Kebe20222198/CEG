"""Tests for CEG Compiler — Human-in-the-Loop (HITL) support.

Validates that:
1. Nodes with `interrupt_before=True` pause execution before running.
2. The workflow can be resumed with approval (`True`) or rejection (`False`).
3. Rejection marks the node as `skipped` and continues the graph cleanly.
4. Nodes with `interrupt_after=True` pause execution after running for review.
5. Review can modify the output before downstream nodes consume it.
6. When no checkpointer is provided, interrupt flags are safely ignored.
"""

import pytest
from langgraph.checkpoint.memory import MemorySaver

from ceg.compiler.compiler import CEGCompiler
from ceg.compiler.mock_executor import MockExecutor
from ceg.models.graph import CEGEdge, CEGGraph, EdgeType
from ceg.models.node import CEGNode


# ── Helpers ──────────────────────────────────────────────────────────


def _hitl_before_graph() -> CEGGraph:
    """Build A → B (interrupt_before) → C."""
    return CEGGraph(
        nodes=[
            CEGNode(id="A", objective="Fetch data"),
            CEGNode(
                id="B",
                objective="Critical operation",
                dependencies=["A"],
                interrupt_before=True,
            ),
            CEGNode(id="C", objective="Send summary", dependencies=["B"]),
        ],
        edges=[
            CEGEdge(source="A", target="B"),
            CEGEdge(source="B", target="C"),
        ],
    )


def _hitl_after_graph() -> CEGGraph:
    """Build A → B (interrupt_after) → C."""
    return CEGGraph(
        nodes=[
            CEGNode(id="A", objective="Fetch data"),
            CEGNode(
                id="B",
                objective="Draft report",
                dependencies=["A"],
                interrupt_after=True,
            ),
            CEGNode(id="C", objective="Publish report", dependencies=["B"]),
        ],
        edges=[
            CEGEdge(source="A", target="B"),
            CEGEdge(source="B", target="C"),
        ],
    )


# ══════════════════════════════════════════════════════════════════════
# HITL Execution tests
# ══════════════════════════════════════════════════════════════════════


class TestHITLInterruptBefore:
    """Tests for interrupt_before behavior."""

    def test_workflow_pauses_before_critical_node(self):
        """Workflow pauses when reaching a node with interrupt_before=True."""
        compiler = CEGCompiler()
        checkpointer = MemorySaver()
        workflow = compiler.compile(_hitl_before_graph(), checkpointer=checkpointer)

        thread_id = "test-thread-1"
        # Invoke runs until the interrupt
        state = workflow.invoke(thread_id=thread_id)

        # Node A completed
        assert state["node_statuses"]["A"] == "completed"
        # Node B has not completed yet
        assert "B" not in state["node_statuses"] or state["node_statuses"].get("B") != "completed"

    def test_workflow_resumes_with_approval(self):
        """Workflow resumes and executes node B when human approves."""
        compiler = CEGCompiler()
        checkpointer = MemorySaver()
        workflow = compiler.compile(_hitl_before_graph(), checkpointer=checkpointer)

        thread_id = "test-thread-2"
        # 1. Initial run -> hits interrupt before B
        workflow.invoke(thread_id=thread_id)

        # 2. Human approves
        final_state = workflow.resume(thread_id=thread_id, value=True)

        # Both B and C should now be completed
        assert final_state["node_statuses"]["B"] == "completed"
        assert final_state["node_statuses"]["C"] == "completed"
        assert final_state["node_outputs"]["B"] is not None
        assert final_state["node_outputs"]["C"] is not None
        assert "B" in final_state["human_approvals"]

    def test_workflow_resumes_with_rejection(self):
        """Workflow marks node B as skipped when human rejects."""
        compiler = CEGCompiler()
        checkpointer = MemorySaver()
        workflow = compiler.compile(_hitl_before_graph(), checkpointer=checkpointer)

        thread_id = "test-thread-3"
        # 1. Initial run -> hits interrupt before B
        workflow.invoke(thread_id=thread_id)

        # 2. Human rejects (value=False)
        final_state = workflow.resume(thread_id=thread_id, value=False)

        # Node B was skipped
        assert final_state["node_statuses"]["B"] == "skipped"
        assert final_state["node_outputs"]["B"] is None
        # Node C still executes (receives None from B)
        assert final_state["node_statuses"]["C"] == "completed"


class TestHITLInterruptAfter:
    """Tests for interrupt_after behavior."""

    def test_workflow_pauses_after_node_execution(self):
        """Workflow pauses after executing a node with interrupt_after=True."""
        compiler = CEGCompiler()
        checkpointer = MemorySaver()
        workflow = compiler.compile(_hitl_after_graph(), checkpointer=checkpointer)

        thread_id = "test-thread-4"
        state = workflow.invoke(thread_id=thread_id)

        # Node A completed, but workflow is paused at node B so C has not run yet
        assert state["node_statuses"]["A"] == "completed"
        assert "C" not in state["node_statuses"]

    def test_workflow_resumes_and_accepts_review(self):
        """Workflow resumes after review and completes node C."""
        compiler = CEGCompiler()
        checkpointer = MemorySaver()
        workflow = compiler.compile(_hitl_after_graph(), checkpointer=checkpointer)

        thread_id = "test-thread-5"
        workflow.invoke(thread_id=thread_id)

        # Human approves after review
        final_state = workflow.resume(
            thread_id=thread_id, value={"approved": True, "comment": "Looks good"}
        )

        assert final_state["node_statuses"]["C"] == "completed"
        assert "after" in final_state["human_approvals"]["B"]

    def test_review_can_modify_output(self):
        """Human can modify node B's output during post-execution review."""
        compiler = CEGCompiler()
        checkpointer = MemorySaver()
        workflow = compiler.compile(_hitl_after_graph(), checkpointer=checkpointer)

        thread_id = "test-thread-6"
        workflow.invoke(thread_id=thread_id)

        # Human provides modified output
        modified = {"node_id": "B", "result": "Human corrected content"}
        final_state = workflow.resume(
            thread_id=thread_id, value={"approved": True, "modified_output": modified}
        )

        assert final_state["node_outputs"]["B"] == modified
        assert final_state["node_statuses"]["C"] == "completed"


class TestHITLBackwardCompatibility:
    """Tests that HITL flags work gracefully without checkpointer."""

    def test_no_checkpointer_ignores_interrupts_safely(self):
        """When compiled without checkpointer, nodes execute without interruption."""
        compiler = CEGCompiler()
        # Compile without checkpointer
        workflow = compiler.compile(_hitl_before_graph())
        result = workflow.invoke()

        # All nodes complete immediately in one shot
        assert result["node_statuses"]["A"] == "completed"
        assert result["node_statuses"]["B"] == "completed"
        assert result["node_statuses"]["C"] == "completed"

    def test_resume_without_checkpointer_raises_error(self):
        """Calling resume() on a workflow compiled without checkpointer raises RuntimeError."""
        compiler = CEGCompiler()
        workflow = compiler.compile(_hitl_before_graph())
        with pytest.raises(RuntimeError, match="no checkpointer configured"):
            workflow.resume("thread-1")

    def test_metadata_has_hitl_flag(self):
        """Compilation metadata indicates HITL nodes are present."""
        compiler = CEGCompiler()
        workflow = compiler.compile(_hitl_before_graph())
        assert workflow.metadata["has_hitl"] is True
