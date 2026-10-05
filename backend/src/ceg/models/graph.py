"""CEGGraph model definition representing a Directed Acyclic Graph (DAG).

Supports sequential, conditional, parallel (fan-out/fan-in), and loop
(controlled cycle) edges. Loop edges are excluded from cycle detection
to allow iterative agent patterns.
"""

from collections import defaultdict, deque
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from ceg.models.node import CEGNode


class EdgeType(str, Enum):
    """Type of edge between two CEG nodes."""

    SEQUENTIAL = "sequential"
    CONDITIONAL = "conditional"
    PARALLEL = "parallel"
    LOOP = "loop"


class CEGEdge(BaseModel):
    """Directed edge between two CEG nodes."""

    source: str = Field(..., min_length=1, description="Source node ID.")
    target: str = Field(..., min_length=1, description="Target node ID.")
    edge_type: EdgeType = Field(
        default=EdgeType.SEQUENTIAL,
        description="Type of edge (sequential, conditional, parallel, loop).",
    )
    condition: str | None = Field(
        default=None,
        description="Condition expression for conditional/loop edges.",
    )
    loop_max_iterations: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Maximum number of loop iterations (required for LOOP edges). "
            "Acts as a safety guard against infinite loops."
        ),
    )


class CEGGraph(BaseModel):
    """Represents a Directed Acyclic Graph (DAG) of CEGNodes.

    Enforces node uniqueness, target existence for edges and dependencies,
    and absence of cycles.
    """

    nodes: list[CEGNode] = Field(
        default_factory=list,
        description="List of nodes composing the cognitive execution graph.",
    )
    edges: list[CEGEdge] = Field(
        default_factory=list,
        description="Directed edges between nodes.",
    )

    @field_validator("edges", mode="before")
    @classmethod
    def _normalize_edges(cls, v: Any) -> Any:
        """Accept both tuple/list and CEGEdge formats for backward compatibility.

        Allows edges to be specified as simple (source, target) tuples, which are
        automatically converted to CEGEdge objects with sequential edge type.
        """
        if not isinstance(v, list):
            return v
        result: list[Any] = []
        for item in v:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                edge_dict: dict[str, Any] = {
                    "source": item[0],
                    "target": item[1],
                }
                if len(item) >= 3:
                    edge_dict["edge_type"] = item[2]
                result.append(edge_dict)
            else:
                result.append(item)
        return result

    @model_validator(mode="after")
    def validate_dag_structure(self) -> "CEGGraph":
        """Validate node uniqueness, dependency references, and absence of cycles."""
        node_ids: set[str] = set()
        duplicate_ids: set[str] = set()

        for node in self.nodes:
            if node.id in node_ids:
                duplicate_ids.add(node.id)
            node_ids.add(node.id)

        if duplicate_ids:
            raise ValueError(
                f"Duplicate node IDs found in CEGGraph: {sorted(duplicate_ids)}"
            )

        adj: dict[str, set[str]] = defaultdict(set)
        in_degree: dict[str, int] = {node_id: 0 for node_id in node_ids}

        # 1. Add edges from node.dependencies (dep -> node.id)
        for node in self.nodes:
            for dep in node.dependencies:
                if dep not in node_ids:
                    msg = (
                        f"Node '{node.id}' references non-existent "
                        f"dependency node '{dep}'."
                    )
                    raise ValueError(msg)
                if node.id not in adj[dep]:
                    adj[dep].add(node.id)

        # 2. Add explicit edges (src -> dst)
        #    LOOP edges are excluded from cycle detection — they create
        #    intentional, controlled cycles with a max_iterations guard.
        for edge in self.edges:
            if edge.source not in node_ids:
                raise ValueError(
                    f"Edge references non-existent source node '{edge.source}'."
                )
            if edge.target not in node_ids:
                raise ValueError(
                    f"Edge references non-existent target node '{edge.target}'."
                )
            # Validate LOOP edge constraints
            if edge.edge_type == EdgeType.LOOP:
                if edge.loop_max_iterations is None:
                    raise ValueError(
                        f"LOOP edge '{edge.source}' → '{edge.target}' must specify "
                        f"loop_max_iterations (safety guard against infinite loops)."
                    )
                # LOOP edges are NOT added to the adjacency for cycle detection
                continue
            adj[edge.source].add(edge.target)

        # Compute in-degrees
        for neighbors in adj.values():
            for dst in neighbors:
                in_degree[dst] += 1

        # 3. Kahn's Algorithm for cycle detection (LOOP edges excluded above)
        queue: deque[str] = deque(
            [node_id for node_id, deg in in_degree.items() if deg == 0]
        )
        visited_count = 0

        while queue:
            current = queue.popleft()
            visited_count += 1
            for neighbor in adj[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited_count != len(node_ids):
            cycle_nodes = sorted(nid for nid, deg in in_degree.items() if deg > 0)
            raise ValueError(
                f"Graph contains a cycle involving nodes {cycle_nodes}. "
                "CEGGraph must be a valid DAG "
                "(use EdgeType.LOOP for intentional cycles)."
            )

        return self


# CEGNode.subgraph refers to CEGGraph, which is only defined now.
CEGNode.model_rebuild(_types_namespace={"CEGGraph": CEGGraph})
CEGGraph.model_rebuild()


# Alias for the compiler interface
CognitiveExecutionGraph = CEGGraph
