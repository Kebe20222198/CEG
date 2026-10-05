"""Evaluation Engine — data models (Pydantic).

Defines the core measurement and reporting types for S5:
  - Criterion          : a single evaluation dimension with LLM-judge prompt
  - CriterionScore     : scored result for one criterion from the judge
  - JudgeVerdict       : full structured verdict returned by a JudgeClient
  - NodeEvaluation     : per-node metric snapshot
  - EvaluationReport   : complete result of one pipeline run evaluation
  - RobustnessReport   : result of N-run robustness measurement
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class Criterion(BaseModel):
    """A single qualitative evaluation dimension.

    Used to instruct a LLM judge on *what* to evaluate and *how* to score it.

    Attributes:
        name: Short identifier (e.g. "alert_relevance").
        description: Human-readable explanation of what the criterion measures.
        weight: Relative importance weight for the composite quality score.
            Multiple criteria weights should sum to 1.0 (not enforced per-criterion
            to allow partial definitions; validated at the verdict aggregation step).
        evaluation_prompt_template: Prompt template sent to the LLM judge.
            Use ``{output}`` as placeholder for the node output under evaluation
            and ``{objective}`` for the node's objective string.
        target_node_ids: Nodes this criterion applies to. Empty means every
            completed node.
    """

    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    weight: float = Field(..., gt=0.0, le=1.0)
    evaluation_prompt_template: str = Field(..., min_length=1)
    target_node_ids: list[str] = Field(default_factory=list)

    def applies_to(self, node_id: str) -> bool:
        """Return True if this criterion evaluates ``node_id``."""
        return not self.target_node_ids or node_id in self.target_node_ids


class CriterionScore(BaseModel):
    """Score produced by the judge for a single Criterion.

    Attributes:
        criterion_name: Name of the evaluated criterion.
        score: Numerical score in [0.0, 1.0].
        justification: Free-text explanation produced by the judge.
        passed: True if score >= threshold (0.5 default).
    """

    criterion_name: str = Field(..., min_length=1)
    score: float = Field(..., ge=0.0, le=1.0)
    justification: str = Field(default="")
    passed: bool = Field(default=True)


class JudgeVerdict(BaseModel):
    """Full structured verdict returned by a JudgeClient for one node output.

    Attributes:
        node_id: The node whose output was judged.
        criteria_scores: One CriterionScore per evaluated Criterion.
        aggregate_quality_score: Weighted average of all criteria scores.
            Computed (as the plain mean of the criteria scores) when not
            given; an explicit value, including 0.0, is kept as is.
        raw_judge_response: Optional raw string returned by the LLM judge
            (useful for debugging; None for mock verdicts).
    """

    node_id: str = Field(..., min_length=1)
    criteria_scores: list[CriterionScore] = Field(default_factory=list)
    aggregate_quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_judge_response: str | None = Field(default=None)

    @model_validator(mode="after")
    def _compute_aggregate(self) -> JudgeVerdict:
        """Compute aggregate_quality_score from the criteria when not given."""
        if (
            self.criteria_scores
            and "aggregate_quality_score" not in self.model_fields_set
        ):
            # Weights are not available at verdict level: plain mean.
            score_sum = sum(cs.score for cs in self.criteria_scores)
            self.aggregate_quality_score = score_sum / len(self.criteria_scores)
        return self


class NodeEvaluation(BaseModel):
    """Per-node metric snapshot captured during evaluation.

    Attributes:
        node_id: Identifier of the evaluated node.
        status: Execution status (completed / skipped / failed).
        cost_usd: Cost incurred by this node in USD.
        latency_ms: Execution latency in milliseconds.
        confidence: Confidence score reported by the executor [0, 1].
        judge_verdict: Optional verdict from the LLM judge (None if skipped
            or if quality evaluation was not run for this node).
    """

    node_id: str = Field(..., min_length=1)
    status: str = Field(default="completed")
    cost_usd: float = Field(default=0.0, ge=0.0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    judge_verdict: JudgeVerdict | None = Field(default=None)


class EvaluationReport(BaseModel):
    """Complete evaluation result for a single pipeline execution.

    Produced by ``EvaluationEngine.evaluate()`` after a workflow invocation.

    Attributes:
        scenario_name: Human-readable label for the test scenario.
        node_evaluations: Per-node metrics list (one entry per executed node).
        total_cost_usd: Sum of all node costs.
        total_latency_ms: Sum of all node latencies.
        total_latency_seconds: ``total_latency_ms / 1000``.
        quality_score: Weighted aggregate quality from the LLM judge [0, 1],
            or None when no node output was judged.
        robustness_score: Fraction of successful runs [0, 1] from an N-run
            measurement, or None when robustness was not measured.
        composite_score: Final composite score computed by the formula:
            wc*(1 - cost/max_budget) + wl*(1 - latency/max_latency)
            + wq*quality + wr*robustness
            Unmeasured dimensions are left out and the remaining weights are
            rescaled to sum to 1 (see ``metadata["unmeasured"]``).
        weights: Dict with keys wc, wl, wq, wr used for composite score.
        max_budget_usd: Budget ceiling used in composite score computation.
        max_latency_seconds: Latency ceiling used in composite score computation.
        metadata: Arbitrary extra context (e.g. fixture path, run count).
    """

    scenario_name: str = Field(default="unnamed")
    node_evaluations: list[NodeEvaluation] = Field(default_factory=list)
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    total_latency_ms: float = Field(default=0.0, ge=0.0)
    total_latency_seconds: float = Field(default=0.0, ge=0.0)
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    robustness_score: float | None = Field(default=None, ge=0.0, le=1.0)
    composite_score: float = Field(default=0.0, ge=0.0, le=1.0)
    weights: dict[str, float] = Field(
        default_factory=lambda: {"wc": 0.25, "wl": 0.25, "wq": 0.25, "wr": 0.25}
    )
    max_budget_usd: float = Field(default=0.50, gt=0.0)
    max_latency_seconds: float = Field(default=15.0, gt=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RobustnessReport(BaseModel):
    """Result of N-run robustness measurement.

    Attributes:
        scenario_name: Human-readable label for the tested scenario.
        n_runs: Total number of runs attempted.
        n_success: Number of runs that completed without fatal error.
        n_failure: Number of runs that raised an exception or ended in error.
        success_rate: n_success / n_runs in [0.0, 1.0].
        failure_reasons: List of error messages from failed runs.
        mean_cost_usd: Mean cost per successful run.
        mean_latency_ms: Mean latency per successful run.
        run_results: Optional list of per-run state dicts (for debugging).
    """

    scenario_name: str = Field(default="unnamed")
    n_runs: int = Field(..., ge=1)
    n_success: int = Field(default=0, ge=0)
    n_failure: int = Field(default=0, ge=0)
    success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    failure_reasons: list[str] = Field(default_factory=list)
    mean_cost_usd: float = Field(default=0.0, ge=0.0)
    mean_latency_ms: float = Field(default=0.0, ge=0.0)
    run_results: list[dict[str, Any]] = Field(default_factory=list)
