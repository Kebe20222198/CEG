"""Cognitive Execution Graph (CEG) package."""

from ceg.compiler.compiler import CEGCompiler, CompiledWorkflow
from ceg.compiler.mock_executor import ExecutionError, ExecutionResult, MockExecutor
from ceg.compiler.state import CEGState
from ceg.models.graph import CEGEdge, CEGGraph, CognitiveExecutionGraph, EdgeType
from ceg.models.node import (
    CEGNode,
    ExecutionRecord,
    ModelTierHint,
    NodeStatus,
)
from ceg.models.task import CognitiveTask, SubTask, TaskConstraint
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
    FallbackPolicy,
    NodeAbortError,
    NodeSkippedError,
)
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

__version__ = "0.5.0"

__all__ = [
    # Compiler
    "CEGCompiler",
    "CEGState",
    "CompiledWorkflow",
    "ExecutionError",
    "ExecutionResult",
    "MockExecutor",
    # Models
    "CEGEdge",
    "CEGGraph",
    "CEGNode",
    "CognitiveExecutionGraph",
    "CognitiveTask",
    "EdgeType",
    "ExecutionRecord",
    "ModelTierHint",
    "NodeStatus",
    "SubTask",
    "TaskConstraint",
    # Runtime — Decision Engine
    "Constraint",
    "DEFAULT_MODEL_REGISTRY",
    "ModelProfile",
    "NoEligibleModelError",
    "RuntimeDecisionEngine",
    "SelectionWeights",
    "select_model",
    # Runtime — Fallback
    "FallbackConfig",
    "FallbackExhaustedError",
    "FallbackPolicy",
    "NodeAbortError",
    "NodeSkippedError",
    # Evaluation Engine
    "Criterion",
    "CriterionScore",
    "EvaluationEngine",
    "EvaluationReport",
    "JudgeClient",
    "JudgeVerdict",
    "MockJudgeClient",
    "NodeEvaluation",
    "RobustnessReport",
    "__version__",
]
