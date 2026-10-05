"""Registry of the pipeline templates a task can be executed with.

A template provides what a declaration cannot: the implementation of its
sub-tasks (the executor), its quality criteria, and the declaration used to
create the demo task. The graph itself always comes from the planner, from
the task stored in the database. Adding a pipeline means adding an entry to
``PIPELINES``; the routers do not change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ceg.compiler.mock_executor import MockExecutor
from ceg.evaluation.models import Criterion
from ceg.models.task import CognitiveTask
from ceg.use_cases.demo_pipelines import (
    HITLBudgetExecutor,
    LoopReportExecutor,
    ParallelSalesExecutor,
    analyse_ventes_parallele,
    redaction_rapport_iteratif,
    validation_budget_hitl,
)
from ceg.use_cases.multi_agent_supervisor import (
    HierarchicalSupervisorExecutor,
    supervision_strategique,
)
from ceg.use_cases.sales_criteria import ALL_SALES_CRITERIA
from ceg.use_cases.sales_pipeline import SalesExecutor, analyse_ventes_alertes


@dataclass(frozen=True)
class PipelineSpec:
    """Implementation and evaluation of one pipeline template.

    Attributes:
        declare: Returns the template's CognitiveTask (used to create and
            refresh the demo task).
        make_executor: Returns a fresh executor (executors may hold state).
        criteria: Quality criteria for the Evaluation Engine.
        uses_csv: The pipeline reads ``inputs["csv_path"]``.
    """

    declare: Callable[[], CognitiveTask]
    make_executor: Callable[[], MockExecutor]
    criteria: list[Criterion] = field(default_factory=list)
    uses_csv: bool = False


PIPELINES: dict[str, PipelineSpec] = {
    "analyse_ventes_alertes": PipelineSpec(
        declare=analyse_ventes_alertes,
        make_executor=SalesExecutor,
        criteria=ALL_SALES_CRITERIA,
        uses_csv=True,
    ),
    "analyse_ventes_parallele": PipelineSpec(
        declare=analyse_ventes_parallele,
        make_executor=ParallelSalesExecutor,
    ),
    "redaction_rapport_iteratif": PipelineSpec(
        declare=redaction_rapport_iteratif,
        make_executor=LoopReportExecutor,
    ),
    "validation_budget_hitl": PipelineSpec(
        declare=validation_budget_hitl,
        make_executor=HITLBudgetExecutor,
    ),
    "multi_agent_supervisor": PipelineSpec(
        declare=supervision_strategique,
        make_executor=HierarchicalSupervisorExecutor,
    ),
}
