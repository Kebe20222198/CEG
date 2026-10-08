"""Cognitive Execution Graph (CEG).

A declarative layer for agentic workflows, independent of the orchestration
framework, the infrastructure and the LLM provider: a CognitiveTask states the
business objective and its constraints; the planner turns it into an
execution graph; the Runtime Decision Engine picks the models; a backend
(LangGraph, plain Python, ...) runs it — much as SQL separates a query from
how the database executes it.
"""

from ceg.backends import (
    DEFAULT_BACKEND,
    Backend,
    Workflow,
    available_backends,
    get_backend,
)
from ceg.compiler.compiler import CEGCompiler, CompiledWorkflow
from ceg.compiler.mock_executor import ExecutionError, ExecutionResult, MockExecutor
from ceg.compiler.state import CEGState
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
from ceg.models.graph import CEGEdge, CEGGraph, CognitiveExecutionGraph, EdgeType
from ceg.models.node import (
    CEGNode,
    ExecutionRecord,
    ModelTierHint,
    NodeStatus,
)
from ceg.models.task import CognitiveTask, RepeatSpec, SubTask, TaskConstraint
from ceg.planner import PlanningError, plan
from ceg.registry import WorkflowDefinition, WorkflowRegistry, workflow
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

__version__ = "0.5.0"

__all__ = [
    # Planner & backends
    "DEFAULT_BACKEND",
    "Backend",
    "PlanningError",
    "Workflow",
    "available_backends",
    "get_backend",
    "plan",
    # Workflows as files
    "WorkflowDefinition",
    "WorkflowRegistry",
    "workflow",
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
    "RepeatSpec",
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
