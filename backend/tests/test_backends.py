"""The same planned task gives the same result on every backend.

This is what "backend-agnostic" means in CEG: the plan does not depend on
the engine that runs it. Each demo pipeline runs on LangGraph and on the
plain-Python backend; statuses, outputs, costs, latencies and the models
chosen for every node must be identical.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from ceg.backends import DEFAULT_BACKEND, available_backends, get_backend
from ceg.compiler.mock_executor import MockExecutor
from ceg.models.graph import CEGGraph
from ceg.runtime.fallback import NodeAbortError
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
from ceg.use_cases.sales_pipeline import SalesExecutor, build_sales_graph

FIXTURES = Path(__file__).parent / "fixtures"

# The sales alert prints its generation date: both backends must see the same
# clock, otherwise two runs straddling a second boundary differ.
FIXED_NOW = datetime(2024, 2, 1, 9, 0, 0)


def _sales_executor() -> MockExecutor:
    return SalesExecutor(clock=lambda: FIXED_NOW)


CASES: dict[str, tuple[Callable[[], CEGGraph], Callable[[], MockExecutor], str]] = {
    "sales_a": (build_sales_graph, _sales_executor, "scenario_a_normal.csv"),
    "sales_b": (build_sales_graph, _sales_executor, "scenario_b_single_anomaly.csv"),
    "sales_c": (
        build_sales_graph,
        _sales_executor,
        "scenario_c_multiple_anomalies.csv",
    ),
    "parallel": (build_parallel_sales_graph, ParallelSalesExecutor, ""),
    "loop": (build_loop_report_graph, LoopReportExecutor, ""),
    "supervisor": (
        build_hierarchical_supervisor_graph,
        HierarchicalSupervisorExecutor,
        "",
    ),
}


def _run(backend: str, case: str) -> dict[str, Any]:
    build, make_executor, csv = CASES[case]
    inputs = {"csv_path": str(FIXTURES / csv)} if csv else {}
    workflow = get_backend(backend).compile(build(), executor=make_executor())
    return workflow.invoke({"inputs": inputs})


def _fingerprint(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "statuses": state["node_statuses"],
        "outputs": state["node_outputs"],
        "cost": round(state["total_cost"], 9),
        "latency": round(state["total_latency_ms"], 6),
        # Execution order of independent branches may differ: compare as a
        # multiset of (node, status, model).
        "log": Counter(
            (e["node_id"], e["status"], e.get("model_used"))
            for e in state["execution_log"]
        ),
    }


class TestRegistry:
    def test_both_backends_are_registered(self) -> None:
        names = {b.name for b in available_backends()}
        assert names == {"langgraph", "python"}
        assert DEFAULT_BACKEND == "langgraph"

    def test_unknown_backend(self) -> None:
        with pytest.raises(ValueError, match="Unknown backend"):
            get_backend("spark")

    def test_capabilities(self) -> None:
        assert get_backend("langgraph").supports_hitl
        assert not get_backend("python").supports_hitl


class TestParity:
    @pytest.mark.parametrize("case", sorted(CASES))
    def test_same_result_on_every_backend(self, case: str) -> None:
        reference = _fingerprint(_run("langgraph", case))
        assert _fingerprint(_run("python", case)) == reference

    def test_python_backend_needs_no_langgraph_objects(self) -> None:
        workflow = get_backend("python").compile(build_parallel_sales_graph())
        assert workflow.metadata["runtime_target"] == "python"
        assert workflow.metadata["has_parallel"]

    @pytest.mark.parametrize("backend", ["langgraph", "python"])
    def test_abort_is_the_same_and_partial_state_is_kept(self, backend: str) -> None:
        graph = build_sales_graph()
        compiler_kwargs: dict[str, Any] = {"executor": SalesExecutor()}
        if backend == "langgraph":
            compiler_kwargs["checkpointer"] = MemorySaver()
        workflow = get_backend(backend).compile(graph, **compiler_kwargs)
        inputs = {"csv_path": str(FIXTURES / "scenario_d_corrupted.csv")}
        with pytest.raises(NodeAbortError) as exc_info:
            workflow.invoke({"inputs": inputs}, thread_id="t1")
        assert exc_info.value.node_id == "fetch_data"
        assert workflow.get_state("t1").values["inputs"] == inputs


class TestPythonBackendLimits:
    def test_hitl_graph_is_refused(self) -> None:
        with pytest.raises(ValueError, match="human approval"):
            get_backend("python").compile(build_hitl_budget_graph())

    def test_hitl_graph_runs_unattended_on_request(self) -> None:
        workflow = get_backend("python").compile(
            build_hitl_budget_graph(),
            executor=HITLBudgetExecutor(),
            ignore_interrupts=True,
        )
        statuses = workflow.invoke()["node_statuses"]
        assert set(statuses.values()) == {"completed"}

    def test_resume_is_not_supported(self) -> None:
        workflow = get_backend("python").compile(build_parallel_sales_graph())
        with pytest.raises(RuntimeError, match="human-in-the-loop"):
            workflow.resume("t1")
