"""CEG Runtime package — model selection, decision engine, and fallback strategies."""

from ceg.runtime.decision_engine import (
    DEFAULT_MODEL_REGISTRY,
    Constraint,
    ModelProfile,
    NoEligibleModelError,
    RuntimeDecisionEngine,
    SelectionWeights,
    select_model,
)
from ceg.runtime.fallback import (
    FallbackConfig,
    FallbackExhaustedError,
    FallbackOrchestrator,
    FallbackPolicy,
    NodeAbortError,
    NodeSkippedError,
    apply_abort,
    apply_degradation,
    apply_escalation,
    apply_retry,
    apply_skip,
)

__all__ = [
    # decision_engine
    "Constraint",
    "DEFAULT_MODEL_REGISTRY",
    "ModelProfile",
    "NoEligibleModelError",
    "RuntimeDecisionEngine",
    "SelectionWeights",
    "select_model",
    # fallback
    "FallbackConfig",
    "FallbackExhaustedError",
    "FallbackOrchestrator",
    "FallbackPolicy",
    "NodeAbortError",
    "NodeSkippedError",
    "apply_abort",
    "apply_degradation",
    "apply_escalation",
    "apply_retry",
    "apply_skip",
]
