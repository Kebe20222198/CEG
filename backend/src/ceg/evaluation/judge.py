"""LLM-as-judge interface and mock implementation.

Architecture:
  - JudgeClient  : Protocol (structural subtyping) — the public contract
  - MockJudgeClient : deterministic implementation for tests (no API calls)
  - (future) OpenAIJudgeClient, AnthropicJudgeClient: drop-in replacements

The JudgeClient.evaluate() contract:
    Input:  node_id, node_output (dict), criteria (list[Criterion]),
            node_objective (str, optional context for the judge)
    Output: JudgeVerdict

Design principle: callers only depend on the Protocol. Swapping the real
judge for a mock (or vice-versa) requires zero changes in EvaluationEngine.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

from ceg.evaluation.models import Criterion, CriterionScore, JudgeVerdict

# ── Protocol ──────────────────────────────────────────────────────────────────


@runtime_checkable
class JudgeClient(Protocol):
    """Structural interface for LLM-as-judge clients.

    Any object implementing ``evaluate()`` with this signature is a valid
    JudgeClient — no inheritance required.
    """

    def evaluate(
        self,
        node_id: str,
        node_output: dict[str, Any],
        criteria: list[Criterion],
        node_objective: str = "",
    ) -> JudgeVerdict:
        """Evaluate a node output against the given criteria.

        Args:
            node_id: Identifier of the node being judged.
            node_output: The output dict produced by the node executor.
            criteria: List of Criterion objects defining what to evaluate.
            node_objective: Optional context — the node's declared objective.

        Returns:
            A JudgeVerdict with one CriterionScore per Criterion.
        """
        ...


# ── MockJudgeClient ───────────────────────────────────────────────────────────


class MockJudgeClient:
    """Deterministic mock judge — no API calls, reproducible scores.

    Scoring strategy (configurable via constructor):
      1. ``fixed_scores`` dict: if ``criterion.name`` is in the dict, use that score.
      2. ``default_score``: fallback score for all other criteria (default 0.9).
      3. Deterministic hash mode (``use_hash=True``): derive score from a hash
         of (node_id, criterion_name) — useful to test non-uniform distributions.

    Justifications are always synthetic strings for traceability in tests.

    Args:
        default_score: Score returned for criteria not in ``fixed_scores``.
        fixed_scores: Mapping of criterion_name → float score override.
        use_hash: If True, derive scores from hash instead of ``default_score``.
        pass_threshold: Minimum score to set ``passed=True`` (default 0.5).
    """

    def __init__(
        self,
        default_score: float = 0.90,
        fixed_scores: dict[str, float] | None = None,
        use_hash: bool = False,
        pass_threshold: float = 0.5,
    ) -> None:
        if not 0.0 <= default_score <= 1.0:
            raise ValueError(f"default_score must be in [0, 1], got {default_score}")
        self.default_score = default_score
        self.fixed_scores: dict[str, float] = fixed_scores or {}
        self.use_hash = use_hash
        self.pass_threshold = pass_threshold

    # ── JudgeClient protocol implementation ──────────────────────────────────

    def evaluate(
        self,
        node_id: str,
        node_output: dict[str, Any],
        criteria: list[Criterion],
        node_objective: str = "",
    ) -> JudgeVerdict:
        """Produce a deterministic JudgeVerdict without any LLM call.

        Each Criterion receives a score from ``_score_criterion()``, a
        synthetic justification, and a ``passed`` flag based on the threshold.
        The score does not depend on the output content: this mock exercises
        the evaluation pipeline, it does not assess quality. Plug a real
        JudgeClient (LLM call) to measure quality.

        The ``aggregate_quality_score`` is the weighted average of all
        criterion scores (using each Criterion's ``weight`` field).
        """
        scores: list[CriterionScore] = []

        for criterion in criteria:
            raw_score = self._score_criterion(node_id, criterion)
            justification = self._build_justification(
                node_id=node_id,
                criterion=criterion,
                score=raw_score,
                node_output=node_output,
                node_objective=node_objective,
            )
            scores.append(
                CriterionScore(
                    criterion_name=criterion.name,
                    score=raw_score,
                    justification=justification,
                    passed=raw_score >= self.pass_threshold,
                )
            )

        aggregate = self._weighted_average(scores, criteria)

        return JudgeVerdict(
            node_id=node_id,
            criteria_scores=scores,
            aggregate_quality_score=aggregate,
            raw_judge_response=None,  # mock — no raw LLM text
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _score_criterion(self, node_id: str, criterion: Criterion) -> float:
        """Return the score for a single criterion.

        Priority:
          1. ``fixed_scores[criterion.name]`` if present
          2. Hash-derived score if ``use_hash=True``
          3. ``default_score``
        """
        if criterion.name in self.fixed_scores:
            return float(self.fixed_scores[criterion.name])

        if self.use_hash:
            return self._hash_score(node_id, criterion.name)

        return self.default_score

    @staticmethod
    def _hash_score(node_id: str, criterion_name: str) -> float:
        """Derive a deterministic score in [0.5, 1.0] from a hash.

        The floor at 0.5 ensures hash-mode scores are always "passing" by
        default, avoiding spurious test failures.
        """
        key = f"{node_id}:{criterion_name}"
        digest = hashlib.md5(key.encode()).hexdigest()  # noqa: S324 (mock only)
        # Map first 4 hex chars to [0.5, 1.0]
        value = int(digest[:4], 16) / 0xFFFF  # in [0.0, 1.0]
        return round(0.5 + value * 0.5, 4)

    @staticmethod
    def _build_justification(
        node_id: str,
        criterion: Criterion,
        score: float,
        node_output: dict[str, Any],
        node_objective: str,
    ) -> str:
        """Build a human-readable synthetic justification string."""
        output_keys = sorted(node_output.keys()) if node_output else []
        return (
            f"[MockJudge] node='{node_id}' criterion='{criterion.name}' "
            f"score={score:.3f} | output_keys={output_keys} | "
            f"objective='{node_objective[:60]}'"
        )

    @staticmethod
    def _weighted_average(
        scores: list[CriterionScore],
        criteria: list[Criterion],
    ) -> float:
        """Compute the weighted average quality score.

        Uses each Criterion's ``weight`` field. If weights do not sum to 1.0,
        the result is normalized by the actual total weight to remain in [0, 1].
        """
        if not scores:
            return 0.0

        weight_map = {c.name: c.weight for c in criteria}
        total_weight = sum(weight_map.get(s.criterion_name, 1.0) for s in scores)

        if total_weight == 0.0:
            return 0.0

        weighted_sum = sum(
            s.score * weight_map.get(s.criterion_name, 1.0) for s in scores
        )
        return round(min(1.0, weighted_sum / total_weight), 6)
