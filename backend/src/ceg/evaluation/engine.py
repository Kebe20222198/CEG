"""EvaluationEngine — measures cost, latency, quality, robustness, and
computes the composite score (Table 7.4 / section 7.5.2).

Usage pattern:
    engine = EvaluationEngine(judge=MockJudgeClient(), criteria=SALES_CRITERIA)
    report = engine.evaluate(
        workflow_state=result,          # dict returned by workflow.invoke()
        scenario_name="scenario_b",
        max_budget_usd=0.50,
        max_latency_seconds=15.0,
    )
    robustness = engine.measure_robustness(
        build_fn=build_sales_graph,
        executor_factory=lambda: SalesExecutor(csv_path="..."),
        scenario_name="scenario_b",
        n_runs=20,
    )
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ceg.evaluation.judge import JudgeClient, MockJudgeClient
from ceg.evaluation.models import (
    Criterion,
    EvaluationReport,
    JudgeVerdict,
    NodeEvaluation,
    RobustnessReport,
)

# ── Default composite score weights ──────────────────────────────────────────

DEFAULT_WEIGHTS: dict[str, float] = {
    "wc": 0.25,  # cost efficiency
    "wl": 0.25,  # latency efficiency
    "wq": 0.25,  # quality (LLM-as-judge)
    "wr": 0.25,  # robustness (N-run success rate)
}


# ── EvaluationEngine ──────────────────────────────────────────────────────────


class EvaluationEngine:
    """Measures cost, latency, quality, and robustness for a CEG pipeline run.

    Each method can be called independently, or ``evaluate()`` orchestrates
    all four measurements into a single ``EvaluationReport``.

    Args:
        judge: A JudgeClient implementation. Defaults to ``MockJudgeClient()``.
        criteria: List of Criterion objects used for quality evaluation.
            Defaults to an empty list (quality is then not measured).
        weights: Composite score weights dict with keys wc, wl, wq, wr.
            Must sum to 1.0. Defaults to 0.25 each.
        quality_node_ids: Node IDs to evaluate with the judge. If None,
            evaluates all nodes with ``status == "completed"``.
    """

    def __init__(
        self,
        judge: JudgeClient | None = None,
        criteria: list[Criterion] | None = None,
        weights: dict[str, float] | None = None,
        quality_node_ids: list[str] | None = None,
    ) -> None:
        self.judge: JudgeClient = judge if judge is not None else MockJudgeClient()
        self.criteria: list[Criterion] = criteria or []
        self.weights: dict[str, float] = weights or dict(DEFAULT_WEIGHTS)
        self.quality_node_ids: list[str] | None = quality_node_ids
        self._validate_weights()

    # ── Public API ────────────────────────────────────────────────────────────

    def measure_cost(self, workflow_state: dict[str, Any]) -> float:
        """Extract total cost in USD from the workflow state.

        Reads ``workflow_state["total_cost"]`` which is accumulated by the
        RuntimeDecisionEngine across all node executions.

        Args:
            workflow_state: Final state dict returned by ``workflow.invoke()``.

        Returns:
            Total cost in USD (float >= 0).
        """
        return float(workflow_state.get("total_cost", 0.0))

    def measure_latency(self, workflow_state: dict[str, Any]) -> float:
        """Extract total latency in milliseconds from the workflow state.

        Reads ``workflow_state["total_latency_ms"]``.

        Args:
            workflow_state: Final state dict returned by ``workflow.invoke()``.

        Returns:
            Total latency in milliseconds (float >= 0).
        """
        return float(workflow_state.get("total_latency_ms", 0.0))

    def measure_quality(
        self,
        workflow_state: dict[str, Any],
        criteria: list[Criterion] | None = None,
    ) -> tuple[float | None, list[JudgeVerdict]]:
        """Run the LLM judge on completed node outputs and return quality score.

        Evaluates all nodes whose status == "completed" (or those listed in
        ``self.quality_node_ids`` if set). Skipped nodes are excluded. Each
        node is judged only on the criteria that apply to it
        (``Criterion.target_node_ids``); a node with none is not judged.
        The aggregate quality score is the mean of per-node aggregate scores.

        Args:
            workflow_state: Final state dict returned by ``workflow.invoke()``.
            criteria: Override criteria list (uses ``self.criteria`` if None).

        Returns:
            Tuple of (aggregate_quality_score: float [0,1], or None when no
                      node was judged, verdicts: list[JudgeVerdict])
        """
        active_criteria = criteria if criteria is not None else self.criteria

        node_outputs: dict[str, Any] = workflow_state.get("node_outputs", {})
        node_statuses: dict[str, str] = workflow_state.get("node_statuses", {})
        execution_log: list[dict[str, Any]] = workflow_state.get("execution_log", [])

        # Build a map of node_id → objective from execution log
        objective_map: dict[str, str] = {
            entry["node_id"]: entry.get("output", {}).get("objective", "")
            if isinstance(entry.get("output"), dict)
            else ""
            for entry in execution_log
        }

        # Determine which nodes to judge
        if self.quality_node_ids is not None:
            candidate_ids = [
                nid
                for nid in self.quality_node_ids
                if node_statuses.get(nid) == "completed"
            ]
        else:
            candidate_ids = [
                nid for nid, status in node_statuses.items() if status == "completed"
            ]

        verdicts: list[JudgeVerdict] = []
        for node_id in candidate_ids:
            node_criteria = [c for c in active_criteria if c.applies_to(node_id)]
            if not node_criteria:
                continue
            output = node_outputs.get(node_id)
            if not isinstance(output, dict):
                output = {}
            verdict = self.judge.evaluate(
                node_id=node_id,
                node_output=output,
                criteria=node_criteria,
                node_objective=objective_map.get(node_id, ""),
            )
            verdicts.append(verdict)

        if not verdicts:
            return None, []

        avg_quality = sum(v.aggregate_quality_score for v in verdicts) / len(verdicts)
        return round(min(1.0, avg_quality), 6), verdicts

    def measure_robustness(
        self,
        build_fn: Callable[[], Any],
        executor_factory: Callable[[], Any],
        scenario_name: str = "unnamed",
        n_runs: int = 20,
        inputs: dict[str, Any] | None = None,
    ) -> RobustnessReport:
        """Execute the pipeline N times and measure success rate.

        A run is counted as successful if ``workflow.invoke()`` returns without
        raising an exception. NodeAbortError and any other exception count as
        failure. Runs are unattended: HITL nodes execute without approval.

        With a deterministic executor every run gives the same outcome, so the
        success rate is 0 or 1; it only becomes informative with executors
        whose failures vary from run to run (real LLM calls, injected faults).

        Args:
            build_fn: Callable that returns a CEGGraph (called once per run).
            executor_factory: Callable that returns a fresh executor each run.
            scenario_name: Label for the report.
            n_runs: Number of runs (default 20; use smaller value in tests).
            inputs: Graph inputs passed to every run (e.g. ``csv_path``).

        Returns:
            RobustnessReport with success_rate, failure_reasons, mean metrics.
        """
        from ceg.compiler.compiler import CEGCompiler

        n_success = 0
        n_failure = 0
        failure_reasons: list[str] = []
        costs: list[float] = []
        latencies: list[float] = []
        run_results: list[dict[str, Any]] = []

        for run_idx in range(n_runs):
            try:
                graph = build_fn()
                executor = executor_factory()
                compiler = CEGCompiler(executor=executor)
                workflow = compiler.compile(graph, ignore_interrupts=True)
                state = workflow.invoke({"inputs": dict(inputs or {})})
                n_success += 1
                costs.append(float(state.get("total_cost", 0.0)))
                latencies.append(float(state.get("total_latency_ms", 0.0)))
                run_results.append(
                    {
                        "run": run_idx,
                        "status": "success",
                        "total_cost": state.get("total_cost", 0.0),
                        "total_latency_ms": state.get("total_latency_ms", 0.0),
                    }
                )
            except Exception as exc:
                n_failure += 1
                reason = f"run {run_idx}: {type(exc).__name__}: {exc}"
                failure_reasons.append(reason)
                run_results.append(
                    {
                        "run": run_idx,
                        "status": "failure",
                        "error": str(exc),
                    }
                )

        success_rate = n_success / n_runs if n_runs > 0 else 0.0
        mean_cost = sum(costs) / len(costs) if costs else 0.0
        mean_latency = sum(latencies) / len(latencies) if latencies else 0.0

        return RobustnessReport(
            scenario_name=scenario_name,
            n_runs=n_runs,
            n_success=n_success,
            n_failure=n_failure,
            success_rate=round(success_rate, 6),
            failure_reasons=failure_reasons,
            mean_cost_usd=round(mean_cost, 6),
            mean_latency_ms=round(mean_latency, 2),
            run_results=run_results,
        )

    def compute_composite_score(
        self,
        cost_usd: float,
        latency_seconds: float,
        quality_score: float | None,
        robustness_score: float | None,
        max_budget_usd: float = 0.50,
        max_latency_seconds: float = 15.0,
        weights: dict[str, float] | None = None,
    ) -> float:
        """Compute the composite score using the spec formula (section 7.5.2).

        Formula:
            score = wc*(1 - cost/max_budget)
                  + wl*(1 - latency/max_latency)
                  + wq*quality_score
                  + wr*robustness_score

        Each term is clipped to [0, 1] before weighting to prevent negative
        contributions from budget/latency overruns. A ``None`` quality or
        robustness means "not measured": that term is left out and the
        remaining weights are rescaled to sum to 1, instead of inventing a
        value for it.

        Args:
            cost_usd: Measured cost in USD.
            latency_seconds: Measured latency in seconds.
            quality_score: Aggregate quality score from judge [0, 1], or None.
            robustness_score: Success rate over N runs [0, 1], or None.
            max_budget_usd: Budget ceiling (denominator for cost term).
            max_latency_seconds: Latency ceiling (denominator for latency term).
            weights: Override weights dict (uses ``self.weights`` if None).

        Returns:
            Composite score in [0.0, 1.0].
        """
        w = weights if weights is not None else self.weights
        wc = w.get("wc", 0.25)
        wl = w.get("wl", 0.25)
        wq = w.get("wq", 0.25)
        wr = w.get("wr", 0.25)

        # (weight, term) pairs — terms clipped to [0, 1]
        terms: list[tuple[float, float]] = [
            (wc, _clip(1.0 - cost_usd / max_budget_usd)),
            (wl, _clip(1.0 - latency_seconds / max_latency_seconds)),
        ]
        if quality_score is not None:
            terms.append((wq, _clip(quality_score)))
        if robustness_score is not None:
            terms.append((wr, _clip(robustness_score)))

        total_weight = sum(weight for weight, _ in terms)
        if total_weight == 0.0:
            return 0.0
        composite = sum(weight * term for weight, term in terms) / total_weight
        return round(_clip(composite), 6)

    def evaluate(
        self,
        workflow_state: dict[str, Any],
        scenario_name: str = "unnamed",
        max_budget_usd: float = 0.50,
        max_latency_seconds: float = 15.0,
        robustness_score: float | None = None,
        criteria: list[Criterion] | None = None,
    ) -> EvaluationReport:
        """Full evaluation pipeline — cost + latency + quality + composite.

        Does NOT run robustness measurement (call ``measure_robustness()``
        separately and pass the result as ``robustness_score``).

        Args:
            workflow_state: Final state dict returned by ``workflow.invoke()``.
            scenario_name: Human-readable label for the scenario.
            max_budget_usd: Budget ceiling for composite score.
            max_latency_seconds: Latency ceiling for composite score.
            robustness_score: Pre-computed robustness score [0, 1], from
                ``measure_robustness()``. Defaults to None: a single run says
                nothing about robustness, so it is reported as unmeasured.
            criteria: Override criteria list.

        Returns:
            EvaluationReport with all metrics populated.
        """
        # 1. Cost & latency
        total_cost_usd = self.measure_cost(workflow_state)
        total_latency_ms = self.measure_latency(workflow_state)
        total_latency_seconds = total_latency_ms / 1000.0

        # 2. Quality (LLM-as-judge)
        quality_score, verdicts = self.measure_quality(workflow_state, criteria)

        # 3. Build verdict map for NodeEvaluation
        verdict_map: dict[str, JudgeVerdict] = {v.node_id: v for v in verdicts}

        # 4. Build per-node evaluations from execution_log
        node_evaluations: list[NodeEvaluation] = []
        execution_log: list[dict[str, Any]] = workflow_state.get("execution_log", [])

        for entry in execution_log:
            node_id = entry.get("node_id", "")
            node_evaluations.append(
                NodeEvaluation(
                    node_id=node_id,
                    status=entry.get("status", "unknown"),
                    cost_usd=float(entry.get("cost", 0.0)),
                    latency_ms=float(entry.get("latency_ms", 0.0)),
                    confidence=float(entry.get("confidence", 0.0)),
                    judge_verdict=verdict_map.get(node_id),
                )
            )

        # 5. Composite score
        composite = self.compute_composite_score(
            cost_usd=total_cost_usd,
            latency_seconds=total_latency_seconds,
            quality_score=quality_score,
            robustness_score=robustness_score,
            max_budget_usd=max_budget_usd,
            max_latency_seconds=max_latency_seconds,
        )

        return EvaluationReport(
            scenario_name=scenario_name,
            node_evaluations=node_evaluations,
            total_cost_usd=total_cost_usd,
            total_latency_ms=total_latency_ms,
            total_latency_seconds=round(total_latency_seconds, 4),
            quality_score=quality_score,
            robustness_score=robustness_score,
            composite_score=composite,
            weights=dict(self.weights),
            max_budget_usd=max_budget_usd,
            max_latency_seconds=max_latency_seconds,
            metadata={
                "scenario": scenario_name,
                "judge": type(self.judge).__name__,
                "unmeasured": [
                    name
                    for name, value in (
                        ("quality", quality_score),
                        ("robustness", robustness_score),
                    )
                    if value is None
                ],
            },
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _validate_weights(self) -> None:
        """Raise ValueError if weights do not sum to 1.0."""
        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"EvaluationEngine weights must sum to 1.0, got {total:.6f}. "
                f"Weights: {self.weights}"
            )


# ── Module-level helper ───────────────────────────────────────────────────────


def _clip(value: float) -> float:
    """Clip a float to [0.0, 1.0]."""
    return max(0.0, min(1.0, value))
