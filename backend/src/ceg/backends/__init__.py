"""Execution backends: where a planned CEGGraph actually runs.

CEG separates the declaration (CognitiveTask), the plan (CEGGraph, from
``ceg.planner``) and the execution engine. A backend turns a plan into a
runnable workflow; every backend shares the node logic and constraint
checks of ``ceg.backends.common``, so the same task gives the same result
whichever backend runs it.

Available backends:
  - ``langgraph``: LangGraph StateGraph (parallel super-steps, checkpoints,
    human-in-the-loop pauses);
  - ``python``: a plain Python interpreter, no framework (no HITL);
  - ``crewai``: a CrewAI Flow (no HITL) — only when the ``crewai`` extra is
    installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ceg.compiler.mock_executor import Executor
    from ceg.models.graph import CEGGraph
    from ceg.runtime.decision_engine import RuntimeDecisionEngine

DEFAULT_BACKEND = "langgraph"


class Workflow(Protocol):
    """A compiled graph, ready to run."""

    metadata: dict[str, Any]

    def invoke(
        self,
        initial_state: dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Run with a fresh budget. Graph inputs go under ``"inputs"``."""
        ...

    def resume(self, thread_id: str, *, value: Any = True) -> dict[str, Any]:
        """Continue a run paused for human approval."""
        ...

    def get_state(self, thread_id: str) -> Any:
        """Saved state of a run; ``.values`` holds the CEGState."""
        ...

    def discard(self, thread_id: str) -> None:
        """Forget the saved state of a finished run."""
        ...


class Backend(Protocol):
    """An execution engine for planned graphs."""

    name: str
    supports_hitl: bool

    def compile(
        self,
        graph: CEGGraph,
        *,
        engine: RuntimeDecisionEngine | None = None,
        executor: Executor | None = None,
        checkpointer: Any | None = None,
        ignore_interrupts: bool = False,
    ) -> Workflow:
        """Check ``graph`` against its task and prepare it to run.

        Raises:
            ValueError: If the graph cannot be run as declared on this
                backend.
        """
        ...


def get_backend(name: str = DEFAULT_BACKEND) -> Backend:
    """Return the backend registered under ``name``.

    Raises:
        ValueError: If no backend has that name.
    """
    backends = _registry()
    if name not in backends:
        raise ValueError(f"Unknown backend '{name}'. Available: {sorted(backends)}.")
    return backends[name]


def available_backends() -> list[Backend]:
    """Every registered backend."""
    return list(_registry().values())


def _registry() -> dict[str, Backend]:
    # Imported here: the LangGraph backend imports the compiler, which itself
    # imports ceg.backends.common.
    from ceg.backends.langgraph import LangGraphBackend
    from ceg.backends.python import PythonBackend

    backends: list[Backend] = [LangGraphBackend(), PythonBackend()]
    try:
        from ceg.backends.crewai import CrewAIBackend
    except ImportError:  # optional extra: pip install -e ".[crewai]"
        pass
    else:
        backends.append(CrewAIBackend())
    return {b.name: b for b in backends}


__all__ = [
    "DEFAULT_BACKEND",
    "Backend",
    "Workflow",
    "available_backends",
    "get_backend",
]
