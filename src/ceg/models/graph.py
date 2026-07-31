"""CEGGraph model definition representing a Directed Acyclic Graph (DAG)."""

from collections import defaultdict, deque

from pydantic import BaseModel, Field, model_validator

from ceg.models.node import CEGNode


class CEGGraph(BaseModel):
    """Represents a Directed Acyclic Graph (DAG) of CEGNodes.

    Enforces node uniqueness, target existence for edges and dependencies,
    and absence of cycles.
    """

    nodes: list[CEGNode] = Field(
        default_factory=list,
        description="List of nodes composing the cognitive execution graph.",
    )
    edges: list[tuple[str, str]] = Field(
        default_factory=list,
        description="Explicit directed edges as (source_id, target_id) tuples.",
    )

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
        for src, dst in self.edges:
            if src not in node_ids:
                raise ValueError(f"Edge references non-existent source node '{src}'.")
            if dst not in node_ids:
                raise ValueError(f"Edge references non-existent target node '{dst}'.")
            adj[src].add(dst)

        # Compute in-degrees
        for src, neighbors in adj.items():
            for dst in neighbors:
                in_degree[dst] += 1

        # 3. Kahn's Algorithm for cycle detection
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
            raise ValueError(
                "Graph contains at least one cycle. CEGGraph must be a valid DAG."
            )

        return self
