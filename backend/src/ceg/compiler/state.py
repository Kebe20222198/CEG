"""CEGState — shared state schema for LangGraph workflows compiled from CEG graphs.

Supports sequential, parallel, loop, and human-in-the-loop control flows.
"""

import operator
from typing import Annotated, Any, TypedDict


def _merge_dicts(current: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Reducer that merges two dicts, with update values overriding current ones."""
    merged = current.copy()
    merged.update(update)
    return merged


class CEGState(TypedDict):
    """Shared state schema for all nodes in a compiled CEG LangGraph workflow.

    Uses LangGraph Annotated reducers to handle incremental state updates
    from each node execution:
    - inputs: graph inputs given to ``invoke()`` (e.g. a CSV path)
    - node_outputs / node_statuses: merged via dict update
    - total_cost / total_latency_ms: accumulated via addition
    - execution_log: appended via list concatenation
    - loop_counts: tracks iterations per loop edge (for cycle control)
    - human_approvals: records human decisions for HITL nodes
    """

    inputs: Annotated[dict[str, Any], _merge_dicts]
    node_outputs: Annotated[dict[str, Any], _merge_dicts]
    node_statuses: Annotated[dict[str, str], _merge_dicts]
    total_cost: Annotated[float, operator.add]
    total_latency_ms: Annotated[float, operator.add]
    execution_log: Annotated[list[dict[str, Any]], operator.add]
    loop_counts: Annotated[dict[str, int], _merge_dicts]
    human_approvals: Annotated[dict[str, Any], _merge_dicts]
