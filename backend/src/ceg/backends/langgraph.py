"""LangGraph backend: runs a CEGGraph as a LangGraph StateGraph."""

from __future__ import annotations

from typing import Any

from ceg.compiler.compiler import CEGCompiler, CompiledWorkflow
from ceg.compiler.mock_executor import Executor
from ceg.models.graph import CEGGraph
from ceg.runtime.decision_engine import RuntimeDecisionEngine


class LangGraphBackend:
    """Backend compiling graphs to LangGraph (see ``CEGCompiler``).

    Supports human-in-the-loop pauses through a LangGraph checkpointer.
    """

    name = "langgraph"
    supports_hitl = True

    def compile(
        self,
        graph: CEGGraph,
        *,
        engine: RuntimeDecisionEngine | None = None,
        executor: Executor | None = None,
        checkpointer: Any | None = None,
        ignore_interrupts: bool = False,
    ) -> CompiledWorkflow:
        """Compile ``graph`` to a LangGraph workflow."""
        compiler = CEGCompiler(engine=engine, executor=executor)
        return compiler.compile(
            graph, checkpointer=checkpointer, ignore_interrupts=ignore_interrupts
        )
