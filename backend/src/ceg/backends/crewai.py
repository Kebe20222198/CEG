"""CrewAI backend: runs a CEGGraph as a CrewAI Flow.

The planned graph is translated into a ``crewai.flow.Flow`` subclass, built
on the fly — the same thing a CrewAI developer would write by hand:

  - every CEG node becomes a Flow method;
  - a node with no predecessor listens to the ``begin`` start method;
  - a dependency becomes ``@listen("<node>")``, a join of several
    dependencies ``@listen(and_(...))``;
  - the source of a LOOP edge gets a ``@router`` that either re-runs the
    loop body (label ``"<target>:run"``) or lets the flow go on
    (``"<source>:done"``);
  - a CONDITIONAL edge is checked when its target is reached: if the
    condition is false the node is skipped, and so is anything it guards.

What a node does (model selection, budgets, fallbacks, state merge) is the
shared logic of ``ceg.backends.common``, so the results are the same as on
the ``langgraph`` and ``python`` backends. CrewAI runs independent listeners
concurrently; the engine reserves every call before it runs, so budgets hold.

Not supported: Human-in-the-Loop pauses (``supports_hitl = False``), and a
loop body whose join waits for a node outside the loop (CrewAI's ``and_``
would wait for it forever on the second iteration): both are refused at
compile time.

Requires the ``crewai`` extra (``pip install -e ".[crewai]"``, Python
3.10–3.13). CrewAI's telemetry and tracing are switched off.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# Before importing crewai: no telemetry, no trace upload, no prompt.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from crewai.flow.flow import Flow, and_, listen, router, start  # noqa: E402

from ceg.backends.common import (  # noqa: E402
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
from ceg.compiler.mock_executor import Executor, MockExecutor  # noqa: E402
from ceg.models.graph import CEGGraph, EdgeType  # noqa: E402
from ceg.runtime.decision_engine import RuntimeDecisionEngine  # noqa: E402

BEGIN = "begin"


class CrewAIBackend:
    """Backend running graphs as CrewAI Flows."""

    name = "crewai"
    supports_hitl = False

    def compile(
        self,
        graph: CEGGraph,
        *,
        engine: RuntimeDecisionEngine | None = None,
        executor: Executor | None = None,
        checkpointer: Any | None = None,
        ignore_interrupts: bool = False,
    ) -> CrewAIWorkflow:
        """Check ``graph`` and translate it into a CrewAI Flow.

        ``checkpointer`` is accepted for interface compatibility and unused.

        Raises:
            ValueError: If the graph is empty, has approval nodes (unless
                ``ignore_interrupts``), cannot honour its task's
                constraints, or has a loop CrewAI cannot express.
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
        return CrewAIWorkflow(graph, engine, executor or MockExecutor())


class CrewAIWorkflow:
    """A graph translated into a CrewAI Flow, ready to run."""

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
        self.flow_class = build_flow_class(graph)
        self.metadata: dict[str, Any] = {
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "runtime_target": CrewAIBackend.name,
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
        """Run the flow to the end with a fresh budget.

        With a ``thread_id``, the final state — or the state reached when a
        node aborts — is kept for ``get_state()``.
        """
        self._engine.reset_budget()
        run = _Run(self._graph, self._engine, self._executor, initial_state(initial))
        try:
            run.kickoff(self.flow_class)
        finally:
            if thread_id is not None:
                self._snapshots[thread_id] = StateSnapshot(values=run.state)
        return run.state

    def resume(self, thread_id: str, *, value: Any = True) -> dict[str, Any]:
        """Not supported: this backend never pauses."""
        raise RuntimeError(
            f"The '{CrewAIBackend.name}' backend does not support "
            "human-in-the-loop: there is nothing to resume."
        )

    def get_state(self, thread_id: str) -> StateSnapshot:
        """State saved for ``thread_id`` (empty if unknown)."""
        return self._snapshots.get(thread_id, StateSnapshot())

    def discard(self, thread_id: str) -> None:
        """Forget the saved state of ``thread_id``."""
        self._snapshots.pop(thread_id, None)


# ── Translation: CEGGraph → Flow subclass ─────────────────────────────────────


class CEGFlow(Flow[dict[str, Any]]):
    """Base of the generated flows: each method delegates to its ``_Run``."""

    def __init__(self, run: _Run) -> None:
        super().__init__(tracing=False, suppress_flow_events=True)
        self.ceg_run = run


def method_names(graph: CEGGraph) -> dict[str, str]:
    """Flow method name of each node: its id, unless that clashes with Flow."""
    names: dict[str, str] = {}
    for node in graph.nodes:
        name = node.id
        if (
            not name.isidentifier()
            or name.startswith("_")
            or name == BEGIN
            or hasattr(CEGFlow, name)
        ):
            name = "node_" + re.sub(r"\W", "_", name).lstrip("_")
        names[node.id] = name
    return names


def build_flow_class(graph: CEGGraph) -> type[CEGFlow]:
    """Translate ``graph`` into a ``CEGFlow`` subclass.

    Raises:
        ValueError: If a loop body joins on a node outside the loop.
    """
    topology = _Topology(graph)
    topology.check_loop_joins()
    names = method_names(graph)

    def signal(node_id: str) -> str:
        # What a successor listens to: the node itself, or — for a loop
        # source — the label its router emits once the loop is over.
        return f"{node_id}:done" if node_id in topology.loops else names[node_id]

    def entry(node_id: str) -> Any:
        preds = sorted(topology.preds[node_id], key=topology.order.__getitem__)
        if not preds:
            return BEGIN
        if len(preds) == 1:
            return signal(preds[0])
        return and_(*(signal(p) for p in preds))

    # Flow's metaclass only sees the methods of the class body itself.
    namespace: dict[str, Any] = {BEGIN: start()(_named(BEGIN, _begin))}

    for node in graph.nodes:
        name = names[node.id]
        step = _named(name, _node_method(node.id))
        if node.id in topology.loop_targets:
            # Entered once through its dependencies, then again by the loop.
            namespace[name] = listen(f"{node.id}:run")(step)
            enter = f"enter_{name}"
            namespace[enter] = router(entry(node.id))(
                _named(enter, _label_method(f"{node.id}:run"))
            )
        else:
            namespace[name] = listen(entry(node.id))(step)

    for source in topology.loops:
        route = f"route_{names[source]}"
        namespace[route] = router(names[source])(_named(route, _loop_method(source)))

    label = (graph.task.name if graph.task else None) or "graph"
    return type("CEGFlow_" + re.sub(r"\W", "_", label), (CEGFlow,), namespace)


def _named(name: str, func: Any) -> Any:
    func.__name__ = func.__qualname__ = name
    return func


def _begin(self: CEGFlow) -> None:
    """Entry point: every node without predecessor listens to it."""


def _node_method(node_id: str) -> Any:
    def run_node(self: CEGFlow) -> None:
        self.ceg_run.run_node(node_id)

    return run_node


def _label_method(label: str) -> Any:
    def emit(self: CEGFlow) -> str:
        return label

    return emit


def _loop_method(source: str) -> Any:
    def route(self: CEGFlow) -> str:
        return self.ceg_run.loop_route(source)

    return route


class _Topology:
    """Predecessors, conditions and loops of a graph (as the python backend)."""

    def __init__(self, graph: CEGGraph) -> None:
        self.order = {n.id: i for i, n in enumerate(graph.nodes)}
        self.succ: dict[str, set[str]] = defaultdict(set)
        for node in graph.nodes:
            for dep in node.dependencies:
                self.succ[dep].add(node.id)
        for edge in graph.edges:
            if edge.edge_type in (EdgeType.SEQUENTIAL, EdgeType.PARALLEL):
                self.succ[edge.source].add(edge.target)

        # target → [(source, condition key)]
        self.guards: dict[str, list[tuple[str, str]]] = defaultdict(list)
        # source → [(condition key, target, counter id, max iterations)]
        self.loops: dict[str, list[tuple[str, str, str, int]]] = defaultdict(list)
        for edge in graph.edges:
            if edge.edge_type == EdgeType.CONDITIONAL:
                self.guards[edge.target].append(
                    (edge.source, edge.condition or "condition")
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
        self.loop_targets = {t for loops in self.loops.values() for _, t, _, _ in loops}

        self.preds: dict[str, set[str]] = defaultdict(set)
        for src, targets in self.succ.items():
            for t in targets:
                self.preds[t].add(src)
        for target, guards in self.guards.items():
            self.preds[target].update(src for src, _ in guards)

    def check_loop_joins(self) -> None:
        for source, loops in self.loops.items():
            for _, target, _, _ in loops:
                body = self._reachable(target, forward=True) & self._reachable(
                    source, forward=False
                )
                for node_id in body - {target}:
                    outside = self.preds[node_id] - body
                    if outside:
                        raise ValueError(
                            f"The '{CrewAIBackend.name}' backend cannot run the "
                            f"loop {target} → {source}: '{node_id}' also waits "
                            f"for {sorted(outside)}, outside the loop."
                        )

    def _reachable(self, start_id: str, *, forward: bool) -> set[str]:
        edges: dict[str, set[str]] = defaultdict(set)
        for target, preds in self.preds.items():
            for p in preds:
                if forward:
                    edges[p].add(target)
                else:
                    edges[target].add(p)
        seen = {start_id}
        stack = [start_id]
        while stack:
            for nxt in edges[stack.pop()] - seen:
                seen.add(nxt)
                stack.append(nxt)
        return seen


# ── One execution ─────────────────────────────────────────────────────────────


class _Run:
    """The CEG state of one run, shared by the methods of its flow.

    CrewAI runs independent listeners in worker threads: the state is
    read and merged under a lock.
    """

    def __init__(
        self,
        graph: CEGGraph,
        engine: RuntimeDecisionEngine,
        executor: Executor,
        state: dict[str, Any],
    ) -> None:
        self.graph = graph
        self.engine = engine
        self.executor = executor
        self.state = state
        self.nodes = {n.id: n for n in graph.nodes}
        self.topology = _Topology(graph)
        self._lock = threading.Lock()

    def kickoff(self, flow_class: type[CEGFlow]) -> None:
        """Run the flow; in its own thread if an event loop already runs here.

        ``Flow.kickoff()`` calls ``asyncio.run()``, which fails inside a
        running loop: a sub-graph started from a flow method (some CrewAI
        versions run methods on the loop), or a caller that is async.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            flow_class(self).kickoff()
            return
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(flow_class(self).kickoff).result()

    def run_node(self, node_id: str) -> None:
        with self._lock:
            state = self.state
        if self._guarded_off(node_id, state):
            update = skip_update(node_id)
        else:
            update = execute_node(
                self.nodes[node_id],
                state,
                self.engine,
                self.executor,
                loop_ids=[lid for _, _, lid, _ in self.topology.loops.get(node_id, [])],
                run_subgraph=self._run_subgraph,
            )
        with self._lock:
            self.state = apply_update(self.state, update)

    def loop_route(self, source: str) -> str:
        """Label emitted after ``source``: loop again, or carry on."""
        with self._lock:
            output = self.state["node_outputs"].get(source)
            counts = self.state.get("loop_counts", {})
        for key, target, lid, max_iterations in self.topology.loops[source]:
            wants = isinstance(output, dict) and bool(output.get(key, False))
            if wants and counts.get(lid, 0) <= max_iterations:
                return f"{target}:run"
        return f"{source}:done"

    def _guarded_off(self, node_id: str, state: dict[str, Any]) -> bool:
        """A condition leading to ``node_id`` is false (or was never evaluated)."""
        outputs = state["node_outputs"]
        return any(
            not (isinstance(outputs.get(src), dict) and outputs[src].get(key))
            for src, key in self.topology.guards.get(node_id, [])
        )

    def _run_subgraph(
        self, subgraph: CEGGraph, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        nested = _Run(
            subgraph, self.engine, self.executor, initial_state({"inputs": inputs})
        )
        nested.kickoff(build_flow_class(subgraph))
        return nested.state
