"""Learned model statistics: the optimiser's knowledge of how models behave.

The static registry gives every model a quality rating per capability, an
estimated cost and an estimated latency. Those are guesses. ``ModelStatistics``
replaces them with what executions actually showed, the way a database
optimiser relies on table statistics rather than on assumptions:

  - **Quality** per (model, capability): the observed quality signal of each
    execution, a failed call counting as quality 0.
  - **Success rate** per (model, capability): failed calls must be retried or
    escalated, so a model that often fails is more expensive and slower than
    its price suggests. Expected cost and latency per *successful* result are
    ``cost / success_rate`` and ``latency / success_rate``.

Every estimate is a Bayesian average between the static value (the prior,
worth ``prior_weight`` pseudo-observations) and the observations, so a model
nobody has used keeps its static rating and evidence takes over gradually.

Exploration: with ``exploration > 0`` the quality used for ranking gets an
upper-confidence bonus that shrinks as a model is observed (UCB). It is
deterministic — the same statistics always give the same choice — so every
decision stays reproducible and explainable.
"""

from __future__ import annotations

import math
import threading
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ceg.runtime.decision_engine import ModelProfile

# Key used when a node requires no particular capability.
ANY_CAPABILITY = "*"

# Prior success rate of a model nobody has observed yet.
PRIOR_SUCCESS_RATE = 0.95


@dataclass
class _Tally:
    """Observations for one (model, capability) pair."""

    n: int = 0
    successes: int = 0
    quality_sum: float = 0.0
    cost_sum: float = 0.0
    latency_sum: float = 0.0


class ModelStatistics:
    """What executions taught about each model, per capability.

    Args:
        prior_weight: How many observations the static values are worth.
            Small → the optimiser trusts evidence quickly.
        exploration: Weight of the UCB exploration bonus on quality (0 =
            pure exploitation of the current estimates).
    """

    def __init__(self, prior_weight: float = 3.0, exploration: float = 0.0) -> None:
        if prior_weight <= 0:
            raise ValueError("prior_weight must be > 0.")
        if exploration < 0:
            raise ValueError("exploration must be >= 0.")
        self.prior_weight = prior_weight
        self.exploration = exploration
        self._tallies: dict[tuple[str, str], _Tally] = {}
        self._lock = threading.Lock()

    # ── Recording ─────────────────────────────────────────────────────

    def observe(
        self,
        model: str,
        capabilities: list[str],
        *,
        success: bool,
        quality: float,
        cost: float,
        latency_ms: float,
    ) -> None:
        """Record one execution of ``model`` on a node needing ``capabilities``.

        Args:
            model: Model name.
            capabilities: The node's required capabilities.
            success: Whether the call produced a usable result.
            quality: Quality signal in [0, 1] (ignored, i.e. 0, on failure).
            cost: What the call cost, failed or not.
            latency_ms: How long the call took, failed or not.
        """
        observed_quality = min(1.0, max(0.0, quality)) if success else 0.0
        with self._lock:
            for capability in capabilities or [ANY_CAPABILITY]:
                tally = self._tallies.setdefault((model, capability), _Tally())
                tally.n += 1
                tally.successes += int(success)
                tally.quality_sum += observed_quality
                tally.cost_sum += cost
                tally.latency_sum += latency_ms

    # ── Estimates ─────────────────────────────────────────────────────

    def observations(self, model: str, capabilities: list[str]) -> int:
        """Fewest observations of ``model`` among ``capabilities``."""
        return min(self._tally(model, c).n for c in capabilities or [ANY_CAPABILITY])

    def quality_estimate(self, model: ModelProfile, capabilities: list[str]) -> float:
        """Expected quality of ``model`` on ``capabilities`` (failures count 0)."""
        caps = capabilities or [ANY_CAPABILITY]
        prior = model.quality_rating_for(capabilities)
        values = [
            self._shrunk(prior, self._tally(model.name, c), "quality") for c in caps
        ]
        return sum(values) / len(values)

    def success_rate(self, model: ModelProfile, capabilities: list[str]) -> float:
        """Probability that a call of ``model`` on ``capabilities`` succeeds."""
        caps = capabilities or [ANY_CAPABILITY]
        values = [
            self._shrunk(PRIOR_SUCCESS_RATE, self._tally(model.name, c), "success")
            for c in caps
        ]
        return sum(values) / len(values)

    def expected_cost(self, model: ModelProfile, capabilities: list[str]) -> float:
        """Expected cost of one *successful* result (failed calls included)."""
        return self._per_success(model, capabilities, "cost", model.estimated_cost)

    def expected_latency_ms(
        self, model: ModelProfile, capabilities: list[str]
    ) -> float:
        """Expected latency of one *successful* result (failed calls included)."""
        return self._per_success(
            model, capabilities, "latency", model.estimated_latency_ms
        )

    def ranking_quality(
        self,
        model: ModelProfile,
        capabilities: list[str],
        candidates: list[ModelProfile],
    ) -> float:
        """Quality used to rank ``model``: estimate + exploration bonus (UCB)."""
        estimate = self.quality_estimate(model, capabilities)
        if self.exploration == 0:
            return estimate
        total = sum(self.observations(m.name, capabilities) for m in candidates)
        n = self.observations(model.name, capabilities)
        return estimate + self.exploration * math.sqrt(math.log(total + 1) / (n + 1))

    # ── Persistence ───────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable snapshot (to store between sessions)."""
        with self._lock:
            return {
                "prior_weight": self.prior_weight,
                "exploration": self.exploration,
                "tallies": [
                    {"model": model, "capability": cap, **asdict(tally)}
                    for (model, cap), tally in sorted(self._tallies.items())
                ],
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelStatistics:
        """Rebuild statistics saved with ``to_dict``."""
        stats = cls(
            prior_weight=data.get("prior_weight", 3.0),
            exploration=data.get("exploration", 0.0),
        )
        for row in data.get("tallies", []):
            row = dict(row)
            key = (row.pop("model"), row.pop("capability"))
            stats._tallies[key] = _Tally(**row)
        return stats

    def summary(self) -> list[dict[str, Any]]:
        """Observed means per (model, capability), for reports."""
        with self._lock:
            return [
                {
                    "model": model,
                    "capability": cap,
                    "n": t.n,
                    "success_rate": round(t.successes / t.n, 3) if t.n else None,
                    "mean_quality": round(t.quality_sum / t.n, 3) if t.n else None,
                }
                for (model, cap), t in sorted(self._tallies.items())
            ]

    # ── Internals ─────────────────────────────────────────────────────

    def _tally(self, model: str, capability: str) -> _Tally:
        return self._tallies.get((model, capability), _Tally())

    def _shrunk(self, prior: float, tally: _Tally, what: str) -> float:
        observed = tally.quality_sum if what == "quality" else float(tally.successes)
        return (self.prior_weight * prior + observed) / (self.prior_weight + tally.n)

    def _per_success(
        self,
        model: ModelProfile,
        capabilities: list[str],
        what: str,
        prior_per_call: float,
    ) -> float:
        caps = capabilities or [ANY_CAPABILITY]
        per_call = []
        for c in caps:
            tally = self._tally(model.name, c)
            observed = tally.cost_sum if what == "cost" else tally.latency_sum
            per_call.append(
                (self.prior_weight * prior_per_call + observed)
                / (self.prior_weight + tally.n)
            )
        mean_per_call = sum(per_call) / len(per_call)
        return mean_per_call / max(self.success_rate(model, capabilities), 1e-3)
