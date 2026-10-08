"""Plain-Python backend: runs a CEGGraph without any orchestration framework.

It exists to keep CEG framework-agnostic: the same planned graph runs here
and on LangGraph, with the same node logic (``ceg.backends.common``), so the
two must give the same results. It is also the simplest backend to read.

Scheduling: a node runs once every predecessor has finished (completed or
skipped). Independent branches run one after the other, in declaration
order — there is no real concurrency. Conditions, skips, bounded loops and
sub-graphs follow the same semantics as the LangGraph backend.

Not supported: Human-in-the-Loop pauses (``supports_hitl = False``). A graph
with approval nodes is refused unless ``ignore_interrupts=True``.

Budgets: cost and latency are accounted by the engine, which reserves each
call before it runs, so both backends enforce the same limits even when
LangGraph runs branches concurrently.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from ceg.backends.common import (
    StateSnapshot,
    apply_update,
    check_graph,
    execute_node,
    hitl_node_ids,
    initial_state,
    loop_id,
    prepare_engine,
    skip_update,
)
from ceg.compiler.mock_executor import Executor, MockExecutor
from ceg.models.graph import CEGGraph, EdgeType
from ceg.runtime.decision_engine import RuntimeDecisionEngine


class PythonBackend:
    """Backend running graphs in a plain Python loop."""

    name = "python"
    supports_hitl = False

    def compile(
        self,
        graph: CEGGraph,
        *,
        engine: RuntimeDecisionEngine | None = None,
        executor: Executor | None = None,
        checkpointer: Any | None = None,
        ignore_interrupts: bool = False,
    ) -> PythonWorkflow:
        """Check ``graph`` and prepare it for execution.

        ``checkpointer`` is accepted for interface compatibility and unused.

        Raises:
            ValueError: If the graph is empty, has approval nodes (unless
                ``ignore_interrupts``), or cannot honour its task's
                constraints.
        """
        if not graph.nodes:
            raise ValueError("CEG graph must have at least one node.")
        hitl_nodes = hitl_node_ids(graph)
        if hitl_nodes and not ignore_interrupts:
            raise ValueError(
                f"The '{self.name}' backend cannot pause for human approval, "
                f"required by nodes {hitl_nodes}. Use a backend that supports "
                "it (langgraph), or ignore_interrupts=True to run them "
                "unattended."
            )
        engine = prepare_engine(graph, engine)
        check_graph(graph, engine)
        return PythonWorkflow(graph, engine, executor or MockExecutor())


class PythonWorkflow:
    """A graph ready to run on the plain-Python backend."""

    def __init__(
        self,
        graph: CEGGraph,
        engine: RuntimeDecisionEngine,
        executor: Executor,
    ) -> None:
        self._graph = graph
        self._engine = engine
        self._executor = executor
        self._snapshots: dict[str, StateSnapshot] = {}
        self.metadata: dict[str, Any] = {
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "runtime_target": PythonBackend.name,
            "has_parallel": any(e.edge_type == EdgeType.PARALLEL for e in graph.edges),
            "has_loops": any(e.edge_type == EdgeType.LOOP for e in graph.edges),
            "has_hitl": bool(hitl_node_ids(graph)),
            "has_subgraphs": any(n.is_subgraph for n in graph.nodes),
        }

    # ── Workflow interface ────────────────────────────────────────────

    def invoke(
        self,
        initial: dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Run the graph to the end with a fresh budget.

        With a ``thread_id``, the final state — or the state reached when a
        node aborts — is kept for ``get_state()``.
        """
        self._engine.reset_budget()
        return self._run(initial_state(initial), thread_id)

    def resume(self, thread_id: str, *, value: Any = True) -> dict[str, Any]:
        """Not supported: this backend never pauses."""
        raise RuntimeError(
            f"The '{PythonBackend.name}' backend does not support "
            "human-in-the-loop: there is nothing to resume."
        )

    def get_state(self, thread_id: str) -> StateSnapshot:
        """State saved for ``thread_id`` (empty if unknown)."""
        return self._snapshots.get(thread_id, StateSnapshot())

    def discard(self, thread_id: str) -> None:
        """Forget the saved state of ``thread_id``."""
        self._snapshots.pop(thread_id, None)

    # ── Interpreter ───────────────────────────────────────────────────

    def _run(self, state: dict[str, Any], thread_id: str | None) -> dict[str, Any]:
        try:
            state = _Interpreter(self._graph, self._engine, self._executor).run(state)
        except _AbortedError as aborted:
            if thread_id is not None:
                self._snapshots[thread_id] = StateSnapshot(values=aborted.state)
            raise aborted.error from None
        if thread_id is not None:
            self._snapshots[thread_id] = StateSnapshot(values=state)
        return state


class _AbortedError(Exception):
    """A node raised: carries the state reached so far and the error."""

    def __init__(self, state: dict[str, Any], error: Exception) -> None:
        super().__init__(str(error))
        self.state = state
        self.error = error


class _Interpreter:
    """One run of a graph: event-driven, a node runs when its inputs are in."""

    def __init__(
        self, graph: CEGGraph, engine: RuntimeDecisionEngine, executor: Executor
    ) -> None:
        self.graph = graph
        self.engine = engine
        self.executor = executor
        self.nodes = {n.id: n for n in graph.nodes}
        self.order = {n.id: i for i, n in enumerate(graph.nodes)}

        # Structural edges: dependencies + SEQUENTIAL + PARALLEL.
        self.succ: dict[str, set[str]] = defaultdict(set)
        for node in graph.nodes:
            for dep in node.dependencies:
                self.succ[dep].add(node.id)
        for edge in graph.edges:
            if edge.edge_type in (EdgeType.SEQUENTIAL, EdgeType.PARALLEL):
                self.succ[edge.source].add(edge.target)

        self.conditional: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.loops: dict[str, list[tuple[str, str, str, int]]] = defaultdict(list)
        for edge in graph.edges:
            if edge.edge_type == EdgeType.CONDITIONAL:
                self.conditional[edge.source].append(
                    (edge.condition or "condition", edge.target)
                )
            elif edge.edge_type == EdgeType.LOOP:
                self.loops[edge.source].append(
                    (
                        edge.condition or "should_loop",
                        edge.target,
                        loop_id(edge),
                        edge.loop_max_iterations or 10,
                    )
                )
        self.guarded: dict[str, set[str]] = {
            src: {t for _, t in branches} for src, branches in self.conditional.items()
        }

        # A node waits for its structural predecessors and its condition source.
        self.preds: dict[str, set[str]] = defaultdict(set)
        for src, targets in self.succ.items():
            for t in targets:
                self.preds[t].add(src)
        for src, targets in self.guarded.items():
            for t in targets:
                self.preds[t].add(src)

        self.arrived: dict[str, set[str]] = defaultdict(set)
        self.queue: deque[str] = deque()

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        for node in self.graph.nodes:
            if not self.preds[node.id]:
                self.queue.append(node.id)
        self.state = state

        while self.queue:
            node_id = self.queue.popleft()
            try:
                update = self._execute(node_id)
            except Exception as exc:
                raise _AbortedError(self.state, exc) from exc
            self.state = apply_update(self.state, update)
            self._route(node_id)
        return self.state

    def _execute(self, node_id: str) -> dict[str, Any]:
        node = self.nodes[node_id]

        def run_subgraph(subgraph: CEGGraph, inputs: dict[str, Any]) -> dict[str, Any]:
            nested = _Interpreter(subgraph, self.engine, self.executor)
            return nested.run(initial_state({"inputs": inputs}))

        return execute_node(
            node,
            self.state,
            self.engine,
            self.executor,
            loop_ids=[lid for _, _, lid, _ in self.loops.get(node_id, [])],
            run_subgraph=run_subgraph,
        )

    # ── Routing after a node finished ─────────────────────────────────

    def _route(self, node_id: str) -> None:
        output = self.state["node_outputs"].get(node_id)
        is_conditional = node_id in self.conditional

        if is_conditional:
            for key, target in self.conditional[node_id]:
                if isinstance(output, dict) and output.get(key):
                    self._arrive(target, node_id)
                else:
                    self._skip(target, node_id)
            for target in self._sorted(self.succ[node_id] - self.guarded[node_id]):
                self._arrive(target, node_id)

        looped = False
        counts = self.state.get("loop_counts", {})
        for key, target, lid, max_iterations in self.loops.get(node_id, []):
            wants = isinstance(output, dict) and bool(output.get(key, False))
            if wants and counts.get(lid, 0) <= max_iterations:
                self._loop_back(target, node_id)
                looped = True
                break

        if not is_conditional and not looped:
            for target in self._sorted(self.succ[node_id]):
                self._arrive(target, node_id)

    def _arrive(self, target: str, source: str) -> None:
        self.arrived[target].add(source)
        if self.arrived[target] >= self.preds[target]:
            self.arrived[target] = set()
            self.queue.append(target)

    def _skip(self, target: str, source: str) -> None:
        """Skip ``target`` and hand over to what it fed into.

        A successor guarded by a condition on the skipped node can no longer
        be evaluated: it is skipped in turn.
        """
        self.arrived[target].add(source)
        if not self.arrived[target] >= self.preds[target]:
            return
        self.arrived[target] = set()
        self.state = apply_update(self.state, skip_update(target))
        for successor in self._sorted(self.guarded.get(target, set())):
            self._skip(successor, target)
        for successor in self._sorted(
            self.succ[target] - self.guarded.get(target, set())
        ):
            self._arrive(successor, target)

    def _loop_back(self, target: str, source: str) -> None:
        """Run the loop body again, from ``target`` up to ``source``."""
        body = self._reachable(target, forward=True) & self._reachable(
            source, forward=False
        )
        for node_id in self.arrived:
            self.arrived[node_id] -= body
        self.queue.append(target)

    def _reachable(self, start: str, *, forward: bool) -> set[str]:
        edges = self.succ if forward else self.preds
        seen = {start}
        stack = [start]
        while stack:
            current = stack.pop()
            neighbours = set(edges.get(current, set()))
            if forward:
                neighbours |= self.guarded.get(current, set())
            for nxt in neighbours - seen:
                seen.add(nxt)
                stack.append(nxt)
        return seen

    def _sorted(self, node_ids: set[str]) -> list[str]:
        return sorted(node_ids, key=lambda n: self.order.get(n, 0))
