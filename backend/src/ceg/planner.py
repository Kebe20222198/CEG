"""Planner: turns a declarative CognitiveTask into an execution graph.

This is CEG's equivalent of a SQL query planner. The task says *what* to do
(objective, sub-tasks, dependencies, conditions, constraints); the planner
decides *how* the work is laid out:

  - one node per sub-task, carrying its objective, capabilities and tier hint;
  - edges from the dependencies — sub-tasks that only depend on the same
    upstream work are fanned out in parallel, without the author saying so;
  - ``run_if``  → conditional edge (the sub-task is skipped when false);
  - ``repeat``  → bounded loop edge;
  - ``requires_approval`` / ``review_output`` → human-in-the-loop points;
  - nested sub-tasks → a sub-graph planned the same way.

The plan is also where declarations are checked: an unknown dependency, a
condition on a sub-task that does not exist or a tool outside
``tools_allowed`` makes the task invalid (``PlanningError``) before anything
runs.

The resulting ``CEGGraph`` is backend-independent; ``ceg.backends`` runs it.
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import ValidationError

from ceg.models.graph import CEGEdge, CEGGraph, EdgeType
from ceg.models.node import CEGNode, ModelTierHint
from ceg.models.task import CognitiveTask, SubTask

MAIN_NODE_ID = "main"


class PlanningError(ValueError):
    """The task declaration cannot be turned into a valid execution graph."""


def plan(task: CognitiveTask) -> CEGGraph:
    """Plan ``task`` into an execution graph.

    A task without sub-tasks becomes a single node carrying its objective.
    The returned graph keeps a reference to ``task`` so that its constraints
    are enforced at run time.

    Raises:
        PlanningError: If the declaration is invalid.
    """
    subtasks = task.subtasks or [SubTask(id=MAIN_NODE_ID, objective=task.objective)]
    graph = plan_subtasks(subtasks, tools_allowed=task.tools_allowed)
    return graph.model_copy(update={"task": task})


def plan_subtasks(subtasks: list[SubTask], *, tools_allowed: list[str]) -> CEGGraph:
    """Plan a list of sub-tasks (a task body or a team) into a graph.

    Raises:
        PlanningError: If the sub-tasks are inconsistent.
    """
    ids = [st.id for st in subtasks]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise PlanningError(f"Duplicate sub-task ids: {duplicates}.")
    known = set(ids)

    conditions = {st.id: _parse_run_if(st, known) for st in subtasks if st.run_if}
    dependencies = {
        st.id: _dependencies_of(st, conditions.get(st.id), known) for st in subtasks
    }

    dependents: dict[str, list[str]] = defaultdict(list)
    for st in subtasks:
        for dep in dependencies[st.id]:
            dependents[dep].append(st.id)

    nodes = [_node_of(st, dependencies[st.id], tools_allowed) for st in subtasks]

    edges: list[CEGEdge] = []
    for st in subtasks:
        condition = conditions.get(st.id)
        for dep in dependencies[st.id]:
            if condition is not None and dep == condition[0]:
                edges.append(
                    CEGEdge(
                        source=dep,
                        target=st.id,
                        edge_type=EdgeType.CONDITIONAL,
                        condition=condition[1],
                    )
                )
                continue
            # Several sub-tasks waiting on the same work run side by side.
            fan_out = len(dependents[dep]) > 1
            edges.append(
                CEGEdge(
                    source=dep,
                    target=st.id,
                    edge_type=EdgeType.PARALLEL if fan_out else EdgeType.SEQUENTIAL,
                )
            )

    for st in subtasks:
        if st.repeat is None:
            continue
        if st.repeat.back_to not in _ancestors(st.id, dependencies) | {st.id}:
            raise PlanningError(
                f"Sub-task '{st.id}' repeats from '{st.repeat.back_to}', which is "
                "not upstream of it."
            )
        edges.append(
            CEGEdge(
                source=st.id,
                target=st.repeat.back_to,
                edge_type=EdgeType.LOOP,
                condition=st.repeat.while_key,
                loop_max_iterations=st.repeat.max_iterations,
            )
        )

    try:
        return CEGGraph(nodes=nodes, edges=edges)
    except ValidationError as exc:
        raise PlanningError(str(exc)) from exc


def _parse_run_if(st: SubTask, known: set[str]) -> tuple[str, str]:
    """Split ``run_if`` into (source sub-task, output key)."""
    source, sep, key = (st.run_if or "").partition(".")
    if not sep or not source or not key:
        raise PlanningError(
            f"Sub-task '{st.id}': run_if must read '<subtask_id>.<output_key>', "
            f"got '{st.run_if}'."
        )
    if source not in known:
        raise PlanningError(
            f"Sub-task '{st.id}': run_if refers to unknown sub-task '{source}'."
        )
    return source, key


def _dependencies_of(
    st: SubTask, condition: tuple[str, str] | None, known: set[str]
) -> list[str]:
    unknown = [d for d in st.dependencies if d not in known]
    if unknown:
        raise PlanningError(f"Sub-task '{st.id}' depends on unknown {unknown}.")
    deps = list(st.dependencies)
    # A condition reads the output of its source: that source runs first.
    if condition is not None and condition[0] not in deps:
        deps.append(condition[0])
    return deps


def _node_of(st: SubTask, dependencies: list[str], tools_allowed: list[str]) -> CEGNode:
    forbidden = [t for t in st.tools if t not in tools_allowed]
    if forbidden:
        raise PlanningError(
            f"Sub-task '{st.id}' uses tools {forbidden} that are not in "
            f"tools_allowed {tools_allowed}."
        )
    if st.model_tier_hint is not None:
        try:
            tier: ModelTierHint | None = ModelTierHint(st.model_tier_hint)
        except ValueError as exc:
            raise PlanningError(
                f"Sub-task '{st.id}': unknown model tier '{st.model_tier_hint}'."
            ) from exc
    else:
        tier = None

    return CEGNode(
        id=st.id,
        objective=st.objective,
        dependencies=dependencies,
        required_capabilities=st.required_capabilities,
        model_tier_hint=tier,
        interrupt_before=st.requires_approval,
        interrupt_after=st.review_output,
        tools=st.tools,
        subgraph=(
            plan_subtasks(st.subtasks, tools_allowed=tools_allowed)
            if st.subtasks
            else None
        ),
    )


def _ancestors(node_id: str, dependencies: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(dependencies.get(node_id, []))
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.add(current)
            stack.extend(dependencies.get(current, []))
    return seen
