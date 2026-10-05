"""CEG Evaluation Engine package — Semaine 5."""

from ceg.evaluation.engine import EvaluationEngine
from ceg.evaluation.judge import JudgeClient, MockJudgeClient
from ceg.evaluation.models import (
    Criterion,
    CriterionScore,
    EvaluationReport,
    JudgeVerdict,
    NodeEvaluation,
    RobustnessReport,
)

__all__ = [
    "EvaluationEngine",
    "JudgeClient",
    "MockJudgeClient",
    "Criterion",
    "CriterionScore",
    "EvaluationReport",
    "JudgeVerdict",
    "NodeEvaluation",
    "RobustnessReport",
]
