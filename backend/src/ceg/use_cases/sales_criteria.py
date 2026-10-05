"""Evaluation criteria for the sales pipeline use case (Semaine 5).

These Criterion objects define the quality dimensions evaluated by the
LLM-as-judge for each pipeline output. They are imported by the tests
and can be passed directly to EvaluationEngine.
"""

from __future__ import annotations

from ceg.evaluation.models import Criterion

# ── Critères pour le nœud detect_anomaly ─────────────────────────────────────

CRITERION_ANOMALY_PRECISION = Criterion(
    name="anomaly_precision",
    description=(
        "Les régions signalées comme anomalies ont bien une chute de volume "
        "supérieure au seuil de -20%. Pas de faux positifs."
    ),
    weight=0.35,
    evaluation_prompt_template=(
        "Objective: {objective}\n"
        "Output: {output}\n\n"
        "Evaluate whether the flagged anomaly regions genuinely exceed the -20% "
        "threshold. Score 1.0 if all anomalies are correctly identified with no "
        "false positives, 0.0 if any region is incorrectly flagged."
    ),
)

CRITERION_ANOMALY_COMPLETENESS = Criterion(
    name="anomaly_completeness",
    description=(
        "Toutes les régions avec une chute > 20% ont été détectées. "
        "Pas de faux négatifs."
    ),
    weight=0.35,
    evaluation_prompt_template=(
        "Objective: {objective}\n"
        "Output: {output}\n\n"
        "Evaluate whether ALL regions with a drop > 20% are included in the "
        "anomaly list. Score 1.0 if no region was missed, 0.0 if any was omitted."
    ),
)

CRITERION_THRESHOLD_ACCURACY = Criterion(
    name="threshold_accuracy",
    description="Le seuil appliqué est exactement -20.0%.",
    weight=0.30,
    evaluation_prompt_template=(
        "Objective: {objective}\n"
        "Output: {output}\n\n"
        "Check that the threshold used is exactly -20.0%. "
        "Score 1.0 if correct, 0.0 otherwise."
    ),
)

# ── Critères pour le nœud generate_alert ─────────────────────────────────────

CRITERION_ALERT_RELEVANCE = Criterion(
    name="alert_relevance",
    description=(
        "L'alerte mentionne toutes les régions en anomalie avec leur "
        "pourcentage de variation."
    ),
    weight=0.40,
    evaluation_prompt_template=(
        "Objective: {objective}\n"
        "Output: {output}\n\n"
        "Evaluate whether the alert message mentions all anomaly regions "
        "with their variation percentages. Score 1.0 if complete, partial score "
        "if some regions are missing."
    ),
)

CRITERION_ALERT_CLARITY = Criterion(
    name="alert_clarity",
    description="Le message d'alerte est clair, lisible et actionnable.",
    weight=0.35,
    evaluation_prompt_template=(
        "Objective: {objective}\n"
        "Output: {output}\n\n"
        "Evaluate the clarity and actionability of the alert message. "
        "Score 1.0 for a clear, well-structured, actionable message."
    ),
)

CRITERION_ALERT_ACCURACY = Criterion(
    name="alert_accuracy",
    description="Les pourcentages et montants dans l'alerte sont corrects.",
    weight=0.25,
    evaluation_prompt_template=(
        "Objective: {objective}\n"
        "Output: {output}\n\n"
        "Verify that the percentages and monetary amounts in the alert are "
        "numerically accurate. Score 1.0 if all values are correct."
    ),
)

# ── Collections exportées ─────────────────────────────────────────────────────

#: Criteria for evaluating the detect_anomaly node output.
ANOMALY_DETECTION_CRITERIA: list[Criterion] = [
    CRITERION_ANOMALY_PRECISION,
    CRITERION_ANOMALY_COMPLETENESS,
    CRITERION_THRESHOLD_ACCURACY,
]

#: Criteria for evaluating the generate_alert node output.
ALERT_GENERATION_CRITERIA: list[Criterion] = [
    CRITERION_ALERT_RELEVANCE,
    CRITERION_ALERT_CLARITY,
    CRITERION_ALERT_ACCURACY,
]

#: All criteria combined (used for full pipeline evaluation).
ALL_SALES_CRITERIA: list[Criterion] = [
    *ANOMALY_DETECTION_CRITERIA,
    *ALERT_GENERATION_CRITERIA,
]
