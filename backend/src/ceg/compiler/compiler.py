"""CEGCompiler — translates a CEG abstract graph into an executable LangGraph workflow.

This module implements the core compilation pipeline:
  validate → topological_sort → inject_runtime → CompiledWorkflow

Supported control-flow mechanisms:
  - SEQUENTIAL: standard linear edge (add_edge)
  - CONDITIONAL: routing function with skip-handler (add_conditional_edges)
  - PARALLEL: fan-out/fan-in — multiple edges from the same source, LangGraph
    executes targets concurrently and waits at the join node.
  - LOOP: controlled back-edge with max_iterations guard — compiled as
    add_conditional_edges that redirects to an earlier node in the graph.
  - HITL: interrupt_before / interrupt_after on nodes — uses LangGraph's
    interrupt() for human-in-the-loop approval with checkpointer persistence.
    Compiling a graph with HITL nodes and no checkpointer is an error, so an
    approval step can never be bypassed by accident.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Hashable
from typing import Any

from langgraph.graph import END, START, StateGraph

from ceg.compiler.mock_executor import Executor, MockExecutor
from ceg.compiler.state import CEGState
from ceg.models.graph import CognitiveExecutionGraph, EdgeType
from ceg.models.node import CEGNode
from ceg.runtime.decision_engine import RuntimeDecisionEngine


class CompiledWorkflow:
    """Wrapper around a compiled LangGraph runnable.

    Provides ``invoke()`` for full execution and ``resume()`` for continuing
    after a Human-in-the-Loop interruption.

    Args:
        runnable: The compiled LangGraph runnable.
        metadata: Compilation metadata (node count, edge count, etc.).
        has_checkpointer: Whether a checkpointer was provided at compile time.
        engine: The RuntimeDecisionEngine wired into the nodes. Its budget is
            reset at the start of every ``invoke()``.
    """

    def __init__(
        self,
        runnable: Any,
        metadata: dict[str, Any] | None = None,
        has_checkpointer: bool = False,
        engine: RuntimeDecisionEngine | None = None,
    ) -> None:
        self._runnable = runnable
        self.metadata: dict[str, Any] = metadata or {}
        self._has_checkpointer = has_checkpointer
        self._engine = engine

    def invoke(
        self,
        initial_state: dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute the compiled workflow end-to-end.

        Args:
            initial_state: Optional overrides merged into the default CEGState.
                Graph inputs go under the ``"inputs"`` key, e.g.
                ``{"inputs": {"csv_path": "data.csv"}}``; executors receive
                them merged with the upstream node outputs.
            thread_id: Thread ID for checkpointed execution (HITL). Required
                when the workflow was compiled with a checkpointer.

        The engine's budget is reset: each invocation is a new execution.

        Returns:
            Final state dict after all nodes have executed (or after an
            interruption if HITL nodes are present).

        Raises:
            ValueError: If the workflow has a checkpointer and no
                ``thread_id`` is given.
        """
        if self._has_checkpointer and not thread_id:
            raise ValueError(
                "This workflow was compiled with a checkpointer: pass a "
                "thread_id to invoke() so that it can be resumed."
            )
        if self._engine is not None:
            self._engine.reset_budget()

        config: dict[str, Any] | None = None
        if self._has_checkpointer:
            config = {"configurable": {"thread_id": thread_id}}

        result: dict[str, Any] = self._runnable.invoke(
            self._initial_state(initial_state), config=config
        )
        return result

    def _invoke_nested(self, initial_state: dict[str, Any]) -> dict[str, Any]:
        """Run as a subgraph from inside a parent node.

        LangGraph hands the parent's run configuration (thread, checkpoint
        namespace) to graphs invoked inside a node, so no thread_id is passed
        and the budget is the parent's, not reset.
        """
        result: dict[str, Any] = self._runnable.invoke(
            self._initial_state(initial_state)
        )
        return result

    @staticmethod
    def _initial_state(overrides: dict[str, Any] | None) -> dict[str, Any]:
        """Default CEGState values, updated with ``overrides``."""
        default_state: dict[str, Any] = {
            "inputs": {},
            "node_outputs": {},
            "node_statuses": {},
            "total_cost": 0.0,
            "total_latency_ms": 0.0,
            "execution_log": [],
            "loop_counts": {},
            "human_approvals": {},
        }
        if overrides:
            default_state.update(overrides)
        return default_state

    def resume(
        self,
        thread_id: str,
        *,
        value: Any = True,
    ) -> dict[str, Any]:
        """Resume a workflow after a Human-in-the-Loop interruption.

        Args:
            thread_id: The thread ID used in the original ``invoke()`` call.
            value: The value to resume with (passed to the interrupted node).
                Defaults to ``True`` (approve). Use ``False`` to reject/skip.

        Returns:
            Final state dict after resumption completes (or hits another interrupt).

        Raises:
            RuntimeError: If no checkpointer was configured at compile time.
        """
        if not self._has_checkpointer:
            raise RuntimeError(
                "Cannot resume: no checkpointer configured. "
                "Pass a checkpointer (e.g. MemorySaver()) to CEGCompiler.compile()."
            )
        from langgraph.types import Command

        config = {"configurable": {"thread_id": thread_id}}
        result: dict[str, Any] = self._runnable.invoke(
            Command(resume=value), config=config
        )
        return result

    def get_state(self, thread_id: str) -> Any:
        """Get the current state of a checkpointed workflow.

        Args:
            thread_id: The thread ID to inspect.

        Returns:
            The LangGraph state snapshot.
        """
        if not self._has_checkpointer:
            raise RuntimeError("Cannot get state: no checkpointer configured.")
        config = {"configurable": {"thread_id": thread_id}}
        return self._runnable.get_state(config)


class CEGCompiler:
    """Compiles a CognitiveExecutionGraph into an executable LangGraph workflow.

    Pipeline:
        1. ``_validate``         — structural checks (all edge types)
        2. ``_topological_sort`` — deterministic execution order (Kahn's, LOOP excluded)
        3. ``_inject_runtime``   — wire RuntimeDecisionEngine + all control flows

    Supports:
        - SEQUENTIAL edges → ``add_edge``
        - CONDITIONAL edges → ``add_conditional_edges`` with skip-handler
        - PARALLEL edges → multiple ``add_edge`` from same source (fan-out/fan-in)
        - LOOP edges → ``add_conditional_edges`` back to earlier node with counter
        - HITL → ``interrupt()`` calls in node functions with checkpointer

    Args:
        runtime_target: Target runtime backend (only ``"langgraph"`` supported).
        engine: Optional pre-configured RuntimeDecisionEngine. If ``None``, a
            default engine is created at compile time with sensible defaults.
        executor: Optional Executor; defaults to a MockExecutor (useful for testing
            with forced failures).
    """

    def __init__(
        self,
        runtime_target: str = "langgraph",
        engine: RuntimeDecisionEngine | None = None,
        executor: Executor | None = None,
    ) -> None:
        if runtime_target != "langgraph":
            raise ValueError(
                f"Unsupported runtime target '{runtime_target}'. "
                "Only 'langgraph' is currently supported."
            )
        self.runtime_target = runtime_target
        self._engine = engine
        self._executor: Executor = executor or MockExecutor()

    # ── public API ────────────────────────────────────────────────────

    def compile(
        self,
        ceg: CognitiveExecutionGraph,
        *,
        checkpointer: Any | None = None,
        ignore_interrupts: bool = False,
    ) -> CompiledWorkflow:
        """Compile a CEG into an executable workflow.

        Args:
            ceg: A validated CognitiveExecutionGraph (CEGGraph) instance.
            checkpointer: LangGraph checkpointer (e.g. MemorySaver) for HITL
                interrupt/resume support. Required when the graph (or one of
                its subgraphs) has interrupt nodes.
            ignore_interrupts: Explicit opt-out for unattended runs such as
                benchmarks: compile a graph with interrupt nodes without a
                checkpointer, and run those nodes without asking anyone.

        Returns:
            A CompiledWorkflow ready to be invoked.

        Raises:
            ValueError: If the graph is invalid, or has interrupt nodes while
                no checkpointer is given and ``ignore_interrupts`` is False.
        """
        self._validate(ceg)
        hitl_nodes = self._hitl_node_ids(ceg)
        if hitl_nodes and checkpointer is None and not ignore_interrupts:
            raise ValueError(
                f"Nodes {hitl_nodes} require human approval "
                "(interrupt_before/interrupt_after) but no checkpointer was "
                "given. Pass checkpointer=MemorySaver() (or another LangGraph "
                "checkpointer), or ignore_interrupts=True to run them unattended."
            )
        engine = self._engine or RuntimeDecisionEngine()
        execution_order = self._topological_sort(ceg)
        workflow = self._inject_runtime(
            ceg, execution_order, engine=engine, checkpointer=checkpointer
        )

        metadata: dict[str, Any] = {
            "node_count": len(ceg.nodes),
            "edge_count": len(ceg.edges),
            "execution_order": execution_order,
            "runtime_target": self.runtime_target,
            "has_parallel": any(e.edge_type == EdgeType.PARALLEL for e in ceg.edges),
            "has_loops": any(e.edge_type == EdgeType.LOOP for e in ceg.edges),
            "has_hitl": any(n.interrupt_before or n.interrupt_after for n in ceg.nodes),
            "has_subgraphs": any(n.is_subgraph for n in ceg.nodes),
        }

        compile_kwargs: dict[str, Any] = {}
        if checkpointer is not None:
            compile_kwargs["checkpointer"] = checkpointer

        return CompiledWorkflow(
            workflow.compile(**compile_kwargs),
            metadata=metadata,
            has_checkpointer=checkpointer is not None,
            engine=engine,
        )

    # ── internal steps ────────────────────────────────────────────────

    @classmethod
    def _hitl_node_ids(cls, ceg: CognitiveExecutionGraph) -> list[str]:
        """Return the IDs of interrupt nodes, including those in subgraphs."""
        found: list[str] = []
        for node in ceg.nodes:
            if node.interrupt_before or node.interrupt_after:
                found.append(node.id)
            if node.subgraph is not None:
                found.extend(cls._hitl_node_ids(node.subgraph))
        return found

    def _validate(self, ceg: CognitiveExecutionGraph) -> None:
        """Validate the CEG for compilation.

        Accepts SEQUENTIAL, CONDITIONAL, PARALLEL, and LOOP edges.
        Performs a defensive acyclicity re-check (LOOP edges excluded).
        """
        if not ceg.nodes:
            raise ValueError("CEG graph must have at least one node.")

        valid_types = {
            EdgeType.SEQUENTIAL,
            EdgeType.CONDITIONAL,
            EdgeType.PARALLEL,
            EdgeType.LOOP,
        }
        for edge in ceg.edges:
            if edge.edge_type not in valid_types:
                raise ValueError(
                    f"CEG Compiler received unsupported edge type "
                    f"'{edge.edge_type.value}' on edge "
                    f"'{edge.source}' → '{edge.target}'."
                )
            # LOOP edges must have max_iterations
            if edge.edge_type == EdgeType.LOOP and edge.loop_max_iterations is None:
                raise ValueError(
                    f"LOOP edge '{edge.source}' → '{edge.target}' must specify "
                    f"loop_max_iterations."
                )

        # Defensive acyclicity check (LOOP edges excluded — they are intentional)
        node_ids = {n.id for n in ceg.nodes}
        adj: dict[str, set[str]] = defaultdict(set)

        # Recursively validate subgraphs if present
        for n in ceg.nodes:
            if n.is_subgraph and n.subgraph is not None:
                self._validate(n.subgraph)

        for node in ceg.nodes:
            for dep in node.dependencies:
                adj[dep].add(node.id)
        for edge in ceg.edges:
            if edge.edge_type == EdgeType.LOOP:
                continue  # Exclude LOOP from cycle detection
            adj[edge.source].add(edge.target)

        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
        for neighbors in adj.values():
            for target in neighbors:
                if target in in_degree:
                    in_degree[target] += 1

        queue: deque[str] = deque(nid for nid, d in in_degree.items() if d == 0)
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for neighbor in adj.get(current, set()):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited != len(node_ids):
            raise ValueError(
                "CEG graph contains a cycle (detected during compilation). "
                "Use EdgeType.LOOP for intentional cycles."
            )

    def _topological_sort(self, ceg: CognitiveExecutionGraph) -> list[str]:
        """Return a deterministic topological ordering of node IDs.

        Uses Kahn's algorithm with sorted tie-breaking so that the output
        is reproducible across runs when multiple valid orderings exist.

        LOOP edges are excluded from the sort (they point backwards and would
        break the topological invariant).
        """
        node_ids = {n.id for n in ceg.nodes}

        # Deduplicated edge set from both dependencies and explicit edges
        # (excluding LOOP edges)
        all_edges: set[tuple[str, str]] = set()
        for node in ceg.nodes:
            for dep in node.dependencies:
                all_edges.add((dep, node.id))
        for edge in ceg.edges:
            if edge.edge_type == EdgeType.LOOP:
                continue  # LOOP edges point backwards
            all_edges.add((edge.source, edge.target))

        adj: dict[str, set[str]] = defaultdict(set)
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}

        for src, dst in all_edges:
            adj[src].add(dst)
            in_degree[dst] += 1

        # Sorted initial queue for deterministic ordering
        queue: deque[str] = deque(sorted(nid for nid, d in in_degree.items() if d == 0))
        result: list[str] = []

        while queue:
            current = queue.popleft()
            result.append(current)
            for neighbor in sorted(adj[current]):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return result

    def _inject_runtime(
        self,
        ceg: CognitiveExecutionGraph,
        order: list[str],
        *,
        engine: RuntimeDecisionEngine,
        checkpointer: Any | None = None,
    ) -> StateGraph[CEGState]:
        """Wire RuntimeDecisionEngine and all control-flow routing into the graph.

        Handles:
        - SEQUENTIAL: standard ``add_edge(src, dst)``
        - CONDITIONAL: ``add_conditional_edges`` with skip-handler
        - PARALLEL: multiple ``add_edge`` from same source (fan-out) with
          convergence at join nodes (fan-in)
        - LOOP: ``add_conditional_edges`` with iteration counter and back-edge
        - HITL: ``interrupt()`` calls in node functions when interrupt_before/after

        Args:
            ceg: The source CEGGraph.
            order: Topological execution order (LOOP edges excluded).
            engine: The RuntimeDecisionEngine shared by every node.
            checkpointer: Optional checkpointer for HITL support.

        Returns:
            A fully wired StateGraph ready to be compiled.
        """
        executor = self._executor
        node_map: dict[str, CEGNode] = {n.id: n for n in ceg.nodes}
        has_checkpointer = checkpointer is not None

        rt_graph: StateGraph[CEGState] = StateGraph(CEGState)

        # ── Register all runtime-wired node functions ─────────────────
        loop_edges = [e for e in ceg.edges if e.edge_type == EdgeType.LOOP]
        loop_ids_by_node: dict[str, list[str]] = defaultdict(list)
        for edge in loop_edges:
            loop_ids_by_node[edge.source].append(f"loop_{edge.source}_{edge.target}")

        for node_id in order:
            node = node_map[node_id]
            rt_graph.add_node(
                node_id,
                self._make_runtime_node_function(
                    node,
                    engine,
                    executor,
                    has_checkpointer=has_checkpointer,
                    checkpointer=checkpointer,
                    loop_ids=loop_ids_by_node.get(node_id, []),
                ),
            )

        # ── Classify edges ────────────────────────────────────────────
        conditional_edges = [
            e for e in ceg.edges if e.edge_type == EdgeType.CONDITIONAL
        ]
        loop_edges = [e for e in ceg.edges if e.edge_type == EdgeType.LOOP]

        # Nodes reachable only through a conditional branch
        conditional_targets: set[str] = {e.target for e in conditional_edges}
        # Nodes that are sources of a conditional edge
        conditional_sources: set[str] = {e.source for e in conditional_edges}
        # Nodes that are sources of a loop back-edge
        loop_sources: set[str] = {e.source for e in loop_edges}

        # Conditional and loop sources own their outgoing routing entirely:
        # adding a plain edge next to ``add_conditional_edges`` would fire both
        # the routed branch and the fallthrough.
        routed_sources: set[str] = conditional_sources | loop_sources

        # ── Collect structural edges (dependencies + SEQUENTIAL + PARALLEL) ──
        # These come from the declared graph, never from consecutive pairs of
        # the topological order: deriving edges from the order chains sibling
        # branches to each other, so a fan-in join node ends up re-executing
        # once per incoming branch instead of waiting for all of them.
        structural_edges: set[tuple[str, str]] = set()
        for node in ceg.nodes:
            for dep in node.dependencies:
                structural_edges.add((dep, node.id))
        for edge in ceg.edges:
            if edge.edge_type in (EdgeType.SEQUENTIAL, EdgeType.PARALLEL):
                structural_edges.add((edge.source, edge.target))

        # ── Wire structural edges ─────────────────────────────────────
        # Multiple edges into the same target is LangGraph's native fan-in:
        # the target waits for every incoming branch and runs exactly once.
        wired_edges: set[tuple[str, str]] = set()
        for src, dst in sorted(structural_edges):
            if src in routed_sources:
                continue
            rt_graph.add_edge(src, dst)
            wired_edges.add((src, dst))

        # ── START → every root node ───────────────────────────────────
        # A root has no structural predecessor. LOOP back-edges do not count
        # as an entry point: their target still needs its normal entry.
        entered: set[str] = {dst for _, dst in structural_edges}
        entered |= conditional_targets
        roots = [nid for nid in order if nid not in entered]
        for root in roots or [order[0]]:
            rt_graph.add_edge(START, root)

        # ── Wire CONDITIONAL edges ────────────────────────────────────
        # Every conditional edge leaving a node shares one router: LangGraph
        # allows a single branch per node, so one ``add_conditional_edges``
        # call per edge raised "Branch already exists" as soon as a node had
        # two guarded successors. The router returns a list of destinations,
        # which also lets several guarded branches fire in the same step.
        conditional_by_source: dict[str, list[Any]] = defaultdict(list)
        for edge in conditional_edges:
            conditional_by_source[edge.source].append(edge)

        # target IDs guarded by a condition, grouped by their source node
        guarded_targets: dict[str, set[str]] = {
            src: {e.target for e in edges_}
            for src, edges_ in conditional_by_source.items()
        }

        def _ensure_skip_node(target_id: str) -> None:
            """Register the skip handler for ``target_id`` and its continuation.

            A skipped node must not sever the rest of the graph: the handler
            hands over to whatever the skipped node fed into. A successor that
            was itself guarded by a condition on the skipped node can no longer
            be evaluated, so it is skipped in turn.
            """
            skip_id = f"__skip__{target_id}"
            if skip_id in rt_graph.nodes:
                return
            rt_graph.add_node(skip_id, self._make_skip_node(target_id))

            successors = sorted(
                dst for src, dst in structural_edges if src == target_id
            )
            if not successors:
                rt_graph.add_edge(skip_id, END)
                return
            for successor in successors:
                if successor in guarded_targets.get(target_id, set()):
                    _ensure_skip_node(successor)
                    rt_graph.add_edge(skip_id, f"__skip__{successor}")
                else:
                    rt_graph.add_edge(skip_id, successor)

        for source, source_edges in conditional_by_source.items():
            conditional_routes: dict[Hashable, str] = {}
            branches: list[tuple[str, str]] = []

            for edge in source_edges:
                _ensure_skip_node(edge.target)
                branches.append((edge.condition or "condition", edge.target))
                conditional_routes[edge.target] = edge.target
                conditional_routes[f"__skip__{edge.target}"] = f"__skip__{edge.target}"

            # Unconditional successors of a conditional source still have to be
            # reached: the structural pass skips routed sources entirely, so the
            # router carries them as always-taken destinations.
            always = sorted(
                dst
                for src, dst in structural_edges
                if src == source and dst not in guarded_targets[source]
            )
            for dst in always:
                conditional_routes[dst] = dst

            rt_graph.add_conditional_edges(
                source,
                self._make_conditional_router(source, branches, always),
                conditional_routes,
            )

        # ── Wire LOOP edges (controlled back-edges) ───────────────────
        # As with conditional edges, every back-edge leaving a node shares one
        # router: LangGraph allows a single branch per node.
        loop_by_source: dict[str, list[Any]] = defaultdict(list)
        for edge in loop_edges:
            loop_by_source[edge.source].append(edge)

        for source, source_loops in loop_by_source.items():
            # The loop exit is the source's declared forward successors, not
            # whatever happens to sit next in the topological order: a sibling
            # branch can occupy that slot, which sends the loop out the wrong
            # way and leaves the real successor unwired.
            exit_targets = sorted(dst for src, dst in structural_edges if src == source)
            if source in conditional_by_source:
                # The conditional router already drives this node's forward
                # path; the loop only decides whether to go back.
                exit_targets = []
            if not exit_targets:
                exit_targets = [END]

            loop_routes: dict[Hashable, str] = {}
            loop_branches: list[tuple[str, str, str, int]] = []
            for edge in source_loops:
                loop_branches.append(
                    (
                        edge.condition or "should_loop",
                        edge.target,
                        f"loop_{edge.source}_{edge.target}",
                        edge.loop_max_iterations or 10,
                    )
                )
                loop_routes[edge.target] = edge.target
            for dst in exit_targets:
                loop_routes[dst] = dst

            rt_graph.add_conditional_edges(
                source,
                self._make_loop_router(source, loop_branches, exit_targets),
                loop_routes,
            )

        # ── Sinks → END ───────────────────────────────────────────────
        # Every node with no outgoing structural edge terminates the graph.
        # Routed sources are excluded: their router already covers every exit.
        has_outgoing: set[str] = {src for src, _ in wired_edges}
        for nid in order:
            if nid in has_outgoing or nid in routed_sources:
                continue
            rt_graph.add_edge(nid, END)

        return rt_graph

    # ── helpers ───────────────────────────────────────────────────────

    def _make_runtime_node_function(
        self,
        node: CEGNode,
        engine: RuntimeDecisionEngine,
        executor: Executor,
        *,
        has_checkpointer: bool = False,
        checkpointer: Any | None = None,
        loop_ids: list[str] | None = None,
    ) -> Any:
        """Create a runtime-wired LangGraph node function.

        The returned closure:
          - Optionally calls ``interrupt()`` before execution (interrupt_before).
          - Calls ``engine.run_node()`` for model selection + execution.
          - Tracks loop iterations if this node is the source of any loop edges.
          - Optionally calls ``interrupt()`` after execution (interrupt_after).
          - Lets ``NodeAbortError`` propagate (LangGraph surfaces it to the caller).

        Args:
            node: The CEGNode this function wraps.
            engine: The shared RuntimeDecisionEngine instance.
            executor: The MockExecutor (may be configured with forced failures).
            has_checkpointer: Whether a checkpointer is available for HITL.
            checkpointer: The checkpointer instance itself, forwarded to nested
                subgraph compilations so inner HITL nodes stay interruptible.
            loop_ids: Optional list of loop IDs where this node is the source.

        Returns:
            A callable ``(state: dict) -> dict`` compatible with LangGraph nodes.
        """
        loops = loop_ids or []

        def _runtime_fn(state: dict[str, Any]) -> dict[str, Any]:
            # ── HITL: interrupt before execution ──────────────────────
            if node.interrupt_before and has_checkpointer:
                from langgraph.types import interrupt

                approval = interrupt(
                    {
                        "node_id": node.id,
                        "objective": node.objective,
                        "action": "approve_before",
                        "message": f"Approval required before executing '{node.id}'",
                    }
                )
                # If the human rejected, skip this node
                if approval is False or (
                    isinstance(approval, dict) and not approval.get("approved", True)
                ):
                    return {
                        "node_outputs": {node.id: None},
                        "node_statuses": {node.id: "skipped"},
                        "total_cost": 0.0,
                        "total_latency_ms": 0.0,
                        "execution_log": [
                            {
                                "node_id": node.id,
                                "status": "skipped",
                                "output": None,
                                "cost": 0.0,
                                "latency_ms": 0.0,
                                "confidence": 0.0,
                                "model_used": None,
                                "reason": "Human rejected before execution",
                            }
                        ],
                        "human_approvals": {node.id: {"before": approval}},
                    }
                # Record approval
                state_update_approval: dict[str, Any] = {
                    "human_approvals": {node.id: {"before": approval}},
                }
            else:
                state_update_approval = {}

            # ── Execute the node (Subgraph or Single Node) ─────────────
            result: dict[str, Any]
            if node.is_subgraph and node.subgraph is not None:
                result = self._run_subgraph(
                    node, node.subgraph, state, engine, executor, checkpointer
                )
            else:
                result = engine.run_node(node=node, state=state, executor=executor)

            # Increment loop counter if this node is a loop source
            if loops:
                loop_counts_update: dict[str, int] = {}
                for lid in loops:
                    current_c = state.get("loop_counts", {}).get(lid, 0)
                    loop_counts_update[lid] = current_c + 1
                result["loop_counts"] = loop_counts_update

            # Merge approval info into result
            if state_update_approval:
                approvals_before: dict[str, Any] = result.setdefault(
                    "human_approvals", {}
                )
                approvals_before.update(state_update_approval["human_approvals"])

            # ── HITL: interrupt after execution ───────────────────────
            if node.interrupt_after and has_checkpointer:
                from langgraph.types import interrupt

                review = interrupt(
                    {
                        "node_id": node.id,
                        "output": result.get("node_outputs", {}).get(node.id),
                        "action": "approve_after",
                        "message": f"Review required for output of '{node.id}'",
                    }
                )
                # Record the review decision
                approvals: dict[str, Any] = result.get("human_approvals", {})
                approvals[node.id] = {
                    **approvals.get(node.id, {}),
                    "after": review,
                }
                result["human_approvals"] = approvals

                # If human modified the output, replace it
                if isinstance(review, dict) and "modified_output" in review:
                    result["node_outputs"] = {node.id: review["modified_output"]}

            return result

        return _runtime_fn

    def _run_subgraph(
        self,
        node: CEGNode,
        subgraph: CognitiveExecutionGraph,
        state: dict[str, Any],
        engine: RuntimeDecisionEngine,
        executor: Executor,
        checkpointer: Any | None,
    ) -> dict[str, Any]:
        """Execute ``subgraph`` as the body of the composite node ``node``.

        The subgraph shares the parent's engine (and so its budget) and
        receives the parent's graph inputs and upstream node outputs as its
        own graph inputs. It reuses the parent's checkpointer so that its HITL
        nodes stay interruptible; the parent's ``compile()`` has already
        refused inner HITL nodes when there is no checkpointer.
        """
        sub_compiler = CEGCompiler(
            runtime_target=self.runtime_target,
            engine=engine,
            executor=executor,
        )
        compiled_sub = sub_compiler.compile(
            subgraph,
            checkpointer=checkpointer,
            ignore_interrupts=checkpointer is None,
        )

        sub_inputs: dict[str, Any] = {
            **state.get("inputs", {}),
            **state.get("node_outputs", {}),
        }
        sub_result = compiled_sub._invoke_nested({"inputs": sub_inputs})

        sub_outputs = sub_result.get("node_outputs", {})
        sub_statuses: dict[str, str] = sub_result.get("node_statuses", {})
        sub_cost = float(sub_result.get("total_cost", 0.0))
        sub_latency = float(sub_result.get("total_latency_ms", 0.0))

        # Tag inner logs with subgraph_parent
        enriched_sub_log: list[dict[str, Any]] = [
            {**entry, "subgraph_parent": node.id}
            for entry in sub_result.get("execution_log", [])
        ]

        # A branch skipped by a condition is a normal outcome, not a failure.
        parent_status = (
            "completed"
            if all(st in ("completed", "skipped") for st in sub_statuses.values())
            else "failed"
        )

        # Summary log entry for the parent node
        enriched_sub_log.append(
            {
                "node_id": node.id,
                "status": parent_status,
                "output": sub_outputs,
                "cost": sub_cost,
                "latency_ms": sub_latency,
                "confidence": 1.0,
                "model_used": "subgraph_composite",
            }
        )

        return {
            "node_outputs": {node.id: sub_outputs},
            "node_statuses": {node.id: parent_status},
            "total_cost": sub_cost,
            "total_latency_ms": sub_latency,
            "execution_log": enriched_sub_log,
        }

    @staticmethod
    def _make_conditional_router(
        source_id: str,
        branches: list[tuple[str, str]],
        always: list[str] | None = None,
    ) -> Any:
        """Return the routing function covering every edge leaving ``source_id``.

        The router reads ``state["node_outputs"][source_id]`` once and decides
        each guarded branch independently, so a node may have any number of
        conditional successors.

        Args:
            source_id: The node whose output drives the routing decision.
            branches: ``(condition_key, target_id)`` pairs, one per conditional
                edge leaving ``source_id``.
            always: Unconditional successors, always included in the result.

        Returns:
            A routing function returning the list of destinations: ``target_id``
            for each branch whose condition is truthy, ``"__skip__" + target_id``
            for the others, plus every entry of ``always``.
        """
        unconditional = list(always or [])

        def _router(state: dict[str, Any]) -> list[str]:
            output = state.get("node_outputs", {}).get(source_id)
            destinations: list[str] = []
            for condition_key, target_id in branches:
                if isinstance(output, dict) and output.get(condition_key):
                    destinations.append(target_id)
                else:
                    destinations.append(f"__skip__{target_id}")
            return destinations + unconditional

        return _router

    @staticmethod
    def _make_loop_router(
        source_id: str,
        branches: list[tuple[str, str, str, int]],
        continue_targets: list[str],
    ) -> Any:
        """Return the routing function for every back-edge leaving ``source_id``.

        Each branch is taken when its condition is truthy in the source node's
        output *and* its iteration budget is not spent. Branches are evaluated
        in declaration order and the first match wins: a node loops back to one
        place, it does not fork.

        Args:
            source_id: The node whose output determines the loop conditions.
            branches: ``(condition_key, target_id, loop_id, max_iterations)``
                tuples, one per LOOP edge leaving ``source_id``.
            continue_targets: Destinations taken when no back-edge applies —
                the source's declared forward successors, or ``[END]``.

        Returns:
            A routing function ``(state) -> list[str]``.
        """

        def _loop_router(state: dict[str, Any]) -> list[str]:
            output = state.get("node_outputs", {}).get(source_id)
            counts = state.get("loop_counts", {})

            for condition_key, target_id, loop_id, max_iterations in branches:
                should_loop = isinstance(output, dict) and bool(
                    output.get(condition_key, False)
                )
                if should_loop and counts.get(loop_id, 0) <= max_iterations:
                    return [target_id]

            return list(continue_targets)

        return _loop_router

    @staticmethod
    def _make_skip_node(target_id: str) -> Any:
        """Return a LangGraph node function that marks ``target_id`` as skipped.

        Used as the "false" branch of a conditional edge: when the condition
        is not met, this handler records a ``skipped`` status for the target
        node and the graph terminates cleanly.
        """

        def _skip_fn(state: dict[str, Any]) -> dict[str, Any]:
            return {
                "node_outputs": {target_id: None},
                "node_statuses": {target_id: "skipped"},
                "total_cost": 0.0,
                "total_latency_ms": 0.0,
                "execution_log": [
                    {
                        "node_id": target_id,
                        "status": "skipped",
                        "output": None,
                        "cost": 0.0,
                        "latency_ms": 0.0,
                        "confidence": 0.0,
                        "model_used": None,
                    }
                ],
            }

        return _skip_fn
