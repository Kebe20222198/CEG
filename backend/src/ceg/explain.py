"""EXPLAIN: what a plan will cost at least, and whether it can be run.

Like SQL's ``EXPLAIN``, this looks at a plan without running it. For each
step it finds the cheapest model the Runtime Decision Engine could pick —
same rules as ``rank_models``: required capabilities, per-call latency
limit, quality floor, tier hint — and adds them up:

  - **minimum cost** and **minimum cumulated latency** of the run. The
    engine accounts latency as the sum of the calls, so this is the bound
    that matters. Steps behind a condition may be skipped and are left out;
    a loop body counts once (it runs at least once); a team counts its
    sub-graph.
  - **errors**: the bounds already exceed the declared budget or latency —
    the task cannot be run as declared, it is refused before anything runs.
  - **warnings**: quality is an estimate, so it never refuses a plan, but a
    step whose models are all expected below ``min_quality_score``, or whose
    tier hint rules out every model that would reach it, is reported.

Estimates use the registry's per-call figures (and the learned quality
estimates when the engine has statistics): actual calls may fail and be
retried, which only costs more. A feasible plan can still abort at run time;
an infeasible one cannot succeed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ceg.backends.common import prepare_engine
from ceg.models.graph import CEGGraph, EdgeType
from ceg.models.node import CEGNode
from ceg.runtime.decision_engine import ModelProfile, RuntimeDecisionEngine


@dataclass(frozen=True)
class StepEstimate:
    """The cheapest way the engine can run one step.

    Attributes:
        node_id: The step (``team/step`` inside a team).
        model: Cheapest model the engine may pick (None for a team).
        cost: Its estimated cost (a team: its sub-graph's minimum).
        latency_ms: Its estimated latency (a team: its sub-graph's minimum).
        quality: Its expected quality on the step's capabilities.
        optional: Behind a condition: may be skipped, not in the bounds.
        team: The step is a sub-graph.
    """

    node_id: str
    model: str | None
    cost: float
    latency_ms: float
    quality: float | None
    optional: bool = False
    team: bool = False


@dataclass
class PlanEstimate:
    """Lower bounds of a plan against its declared constraints."""

    steps: list[StepEstimate]
    min_cost: float
    min_latency_ms: float
    max_cost_usd: float
    max_latency_ms: float | None
    min_quality: float
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def feasible(self) -> bool:
        """No declared limit is exceeded by the plan's minimum."""
        return not self.errors


def explain(
    graph: CEGGraph, engine: RuntimeDecisionEngine | None = None
) -> PlanEstimate:
    """Estimate the minimum cost and latency of ``graph`` under its task.

    ``engine`` supplies the models, the per-call latency limit and, for the
    learned optimiser, its statistics. It is bound by the task's constraints
    as at run time.
    """
    engine = prepare_engine(graph, engine)
    estimate = PlanEstimate(
        steps=[],
        min_cost=0.0,
        min_latency_ms=0.0,
        max_cost_usd=engine.budget_total,
        max_latency_ms=engine.max_total_latency_ms,
        min_quality=engine.min_quality,
    )
    estimate.min_cost, estimate.min_latency_ms = _walk(graph, engine, estimate)

    if estimate.min_cost > estimate.max_cost_usd + 1e-9:
        estimate.errors.append(
            f"Budget insuffisant : le plan coûte au moins {estimate.min_cost:.4f} $ "
            f"pour {estimate.max_cost_usd} $ déclarés."
        )
    limit = estimate.max_latency_ms
    if limit is not None and estimate.min_latency_ms > limit + 1e-6:
        estimate.errors.append(
            f"Latence insuffisante : le plan prend au moins "
            f"{estimate.min_latency_ms:.0f} ms pour {limit:.0f} ms déclarées."
        )
    return estimate


def _walk(
    graph: CEGGraph,
    engine: RuntimeDecisionEngine,
    estimate: PlanEstimate,
    *,
    prefix: str = "",
) -> tuple[float, float]:
    optional = {e.target for e in graph.edges if e.edge_type == EdgeType.CONDITIONAL}
    cost = latency = 0.0
    for node in graph.nodes:
        if node.subgraph is not None:
            team_cost, team_latency = _walk(
                node.subgraph, engine, estimate, prefix=f"{prefix}{node.id}/"
            )
            step = StepEstimate(
                node_id=f"{prefix}{node.id}",
                model=None,
                cost=team_cost,
                latency_ms=team_latency,
                quality=None,
                optional=node.id in optional,
                team=True,
            )
        else:
            step = _step(node, engine, estimate, prefix, node.id in optional)
        estimate.steps.append(step)
        if not step.optional:
            cost += step.cost
            latency += step.latency_ms
    return cost, latency


def _step(
    node: CEGNode,
    engine: RuntimeDecisionEngine,
    estimate: PlanEstimate,
    prefix: str,
    optional: bool,
) -> StepEstimate:
    node_id = f"{prefix}{node.id}"
    caps = node.required_capabilities

    def quality(model: ModelProfile) -> float:
        if engine.statistics is not None:
            return engine.statistics.quality_estimate(model, caps)
        return model.quality_rating_for(caps)

    candidates = [
        m
        for m in engine.available_models
        if m.supports(caps) and m.estimated_latency_ms <= engine.max_latency_ms
    ]
    if not candidates:
        estimate.errors.append(f"« {node_id} » : aucun modèle ne peut l'exécuter.")
        return StepEstimate(node_id, None, 0.0, 0.0, None, optional)

    floor = engine.min_quality
    best = max(candidates, key=quality)
    hint = node.model_tier_hint
    if hint is None:
        good_enough = [m for m in candidates if quality(m) >= floor]
        if floor > 0 and not good_enough:
            estimate.warnings.append(
                f"« {node_id} » : aucun modèle n'atteint la qualité {floor} "
                f"(meilleur : {best.name}, {quality(best):.2f})."
            )
        candidates = good_enough or candidates
    else:
        in_tier = [m for m in candidates if m.tier == hint]
        if in_tier:
            candidates = in_tier
            reachable = max(quality(m) for m in in_tier)
            if floor > 0 and reachable < floor:
                estimate.warnings.append(
                    f"« {node_id} » : l'indication « {hint.value} » ne permet pas "
                    f"d'atteindre la qualité {floor} (au mieux {reachable:.2f} ; "
                    f"{best.name} atteindrait {quality(best):.2f})."
                )

    cheapest = min(candidates, key=lambda m: (m.estimated_cost, m.estimated_latency_ms))
    return StepEstimate(
        node_id=node_id,
        model=cheapest.name,
        cost=cheapest.estimated_cost,
        latency_ms=min(m.estimated_latency_ms for m in candidates),
        quality=round(quality(cheapest), 3),
        optional=optional,
    )
