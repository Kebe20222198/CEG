"""Registry of the pipeline templates a task can be executed with.

A task names its template in ``TaskModel.pipeline``. Adding a pipeline means
adding an entry to ``PIPELINES``; the routers do not change. A task without a
template is executed as the graph described by its own subtasks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ceg.compiler.mock_executor import MockExecutor
from ceg.evaluation.models import Criterion
from ceg.models.graph import CEGGraph
from ceg.models.node import CEGNode, ModelTierHint
from ceg.models.task import SubTask
from ceg.use_cases.demo_pipelines import (
    HITLBudgetExecutor,
    LoopReportExecutor,
    ParallelSalesExecutor,
    build_hitl_budget_graph,
    build_loop_report_graph,
    build_parallel_sales_graph,
)
from ceg.use_cases.multi_agent_supervisor import (
    HierarchicalSupervisorExecutor,
    build_hierarchical_supervisor_graph,
)
from ceg.use_cases.sales_criteria import ALL_SALES_CRITERIA
from ceg.use_cases.sales_pipeline import SalesExecutor, build_sales_graph


@dataclass(frozen=True)
class PipelineSpec:
    """How to build, run and judge one pipeline template.

    Attributes:
        build_graph: Returns a fresh CEGGraph.
        make_executor: Returns a fresh executor (executors may hold state).
        criteria: Quality criteria for the Evaluation Engine.
        uses_csv: The pipeline reads ``inputs["csv_path"]``.
    """

    build_graph: Callable[[], CEGGraph]
    make_executor: Callable[[], MockExecutor]
    criteria: list[Criterion] = field(default_factory=list)
    uses_csv: bool = False


PIPELINES: dict[str, PipelineSpec] = {
    "analyse_ventes_alertes": PipelineSpec(
        build_graph=build_sales_graph,
        make_executor=SalesExecutor,
        criteria=ALL_SALES_CRITERIA,
        uses_csv=True,
    ),
    "analyse_ventes_parallele": PipelineSpec(
        build_graph=build_parallel_sales_graph,
        make_executor=ParallelSalesExecutor,
    ),
    "redaction_rapport_iteratif": PipelineSpec(
        build_graph=build_loop_report_graph,
        make_executor=LoopReportExecutor,
    ),
    "validation_budget_hitl": PipelineSpec(
        build_graph=build_hitl_budget_graph,
        make_executor=HITLBudgetExecutor,
    ),
    "multi_agent_supervisor": PipelineSpec(
        build_graph=build_hierarchical_supervisor_graph,
        make_executor=HierarchicalSupervisorExecutor,
    ),
}


def graph_from_subtasks(objective: str, subtasks: list[SubTask]) -> CEGGraph:
    """Build the graph of a task without template: one node per subtask.

    Dependencies come from ``SubTask.dependencies``. A task without subtasks
    becomes a single node carrying the task objective.

    Raises:
        ValueError: If the subtasks do not form a valid DAG or name an
            unknown model tier.
    """
    if not subtasks:
        return CEGGraph(nodes=[CEGNode(id="main", objective=objective)])
    return CEGGraph(
        nodes=[
            CEGNode(
                id=st.id,
                objective=st.objective,
                required_capabilities=st.required_capabilities,
                model_tier_hint=(
                    ModelTierHint(st.model_tier_hint) if st.model_tier_hint else None
                ),
                dependencies=st.dependencies,
            )
            for st in subtasks
        ]
    )
