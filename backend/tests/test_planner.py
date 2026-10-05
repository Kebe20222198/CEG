"""Tests for the planner: declarative CognitiveTask → CEGGraph."""

from __future__ import annotations

from typing import Any

import pytest

from ceg.models.graph import CEGGraph, EdgeType
from ceg.models.node import ModelTierHint
from ceg.models.task import CognitiveTask, SubTask, TaskConstraint
from ceg.planner import MAIN_NODE_ID, PlanningError, plan
from ceg.use_cases.demo_pipelines import (
    analyse_ventes_parallele,
    redaction_rapport_iteratif,
    validation_budget_hitl,
)
from ceg.use_cases.multi_agent_supervisor import supervision_strategique
from ceg.use_cases.sales_pipeline import analyse_ventes_alertes


def _task(*subtasks: dict[str, Any], **fields: Any) -> CognitiveTask:
    return CognitiveTask(
        objective="o",
        subtasks=[SubTask.model_validate(st) for st in subtasks],
        **fields,
    )


def _edges(graph: CEGGraph) -> set[tuple[str, str, str]]:
    return {(e.source, e.target, e.edge_type.value) for e in graph.edges}


class TestPlanStructure:
    def test_task_without_subtasks_is_a_single_node(self) -> None:
        graph = plan(CognitiveTask(objective="Résumer le rapport"))
        assert [n.id for n in graph.nodes] == [MAIN_NODE_ID]
        assert graph.nodes[0].objective == "Résumer le rapport"

    def test_graph_keeps_its_declaration(self) -> None:
        task = _task({"id": "a", "objective": "a"})
        assert plan(task).task == task

    def test_dependencies_become_sequential_edges(self) -> None:
        graph = plan(
            _task(
                {"id": "a", "objective": "a"},
                {"id": "b", "objective": "b", "dependencies": ["a"]},
            )
        )
        assert _edges(graph) == {("a", "b", "sequential")}

    def test_independent_subtasks_are_planned_in_parallel(self) -> None:
        """Nobody asks for parallelism: the planner infers it."""
        graph = plan(
            _task(
                {"id": "a", "objective": "a"},
                {"id": "b", "objective": "b", "dependencies": ["a"]},
                {"id": "c", "objective": "c", "dependencies": ["a"]},
                {"id": "d", "objective": "d", "dependencies": ["b", "c"]},
            )
        )
        assert _edges(graph) == {
            ("a", "b", "parallel"),
            ("a", "c", "parallel"),
            ("b", "d", "sequential"),
            ("c", "d", "sequential"),
        }

    def test_run_if_becomes_a_conditional_edge_and_a_dependency(self) -> None:
        graph = plan(
            _task(
                {"id": "detect", "objective": "d"},
                {"id": "alert", "objective": "a", "run_if": "detect.found"},
            )
        )
        (edge,) = graph.edges
        assert (edge.source, edge.target, edge.edge_type) == (
            "detect",
            "alert",
            EdgeType.CONDITIONAL,
        )
        assert edge.condition == "found"
        assert graph.nodes[1].dependencies == ["detect"]

    def test_repeat_becomes_a_bounded_loop_edge(self) -> None:
        graph = plan(
            _task(
                {"id": "draft", "objective": "d"},
                {
                    "id": "review",
                    "objective": "r",
                    "dependencies": ["draft"],
                    "repeat": {
                        "back_to": "draft",
                        "while_key": "redo",
                        "max_iterations": 2,
                    },
                },
            )
        )
        loop = next(e for e in graph.edges if e.edge_type == EdgeType.LOOP)
        assert (loop.source, loop.target, loop.condition) == ("review", "draft", "redo")
        assert loop.loop_max_iterations == 2

    def test_node_attributes_come_from_the_subtask(self) -> None:
        graph = plan(
            _task(
                {
                    "id": "pay",
                    "objective": "Payer",
                    "model_tier_hint": "quality",
                    "required_capabilities": ["reasoning"],
                    "requires_approval": True,
                    "review_output": True,
                    "tools": ["bank"],
                },
                tools_allowed=["bank"],
            )
        )
        node = graph.nodes[0]
        assert node.model_tier_hint == ModelTierHint.QUALITY
        assert node.required_capabilities == ["reasoning"]
        assert node.interrupt_before and node.interrupt_after
        assert node.tools == ["bank"]

    def test_nested_subtasks_become_a_subgraph(self) -> None:
        graph = plan(
            _task(
                {
                    "id": "team",
                    "objective": "t",
                    "subtasks": [
                        {"id": "x", "objective": "x"},
                        {"id": "y", "objective": "y", "dependencies": ["x"]},
                    ],
                }
            )
        )
        sub = graph.nodes[0].subgraph
        assert sub is not None
        assert [n.id for n in sub.nodes] == ["x", "y"]


class TestPlanRefusals:
    def test_unknown_dependency(self) -> None:
        with pytest.raises(PlanningError, match="unknown"):
            plan(_task({"id": "a", "objective": "a", "dependencies": ["ghost"]}))

    def test_malformed_run_if(self) -> None:
        with pytest.raises(PlanningError, match="run_if"):
            plan(_task({"id": "a", "objective": "a", "run_if": "no_key"}))

    def test_run_if_on_unknown_subtask(self) -> None:
        with pytest.raises(PlanningError, match="unknown sub-task"):
            plan(_task({"id": "a", "objective": "a", "run_if": "ghost.ok"}))

    def test_repeat_back_to_must_be_upstream(self) -> None:
        with pytest.raises(PlanningError, match="not upstream"):
            plan(
                _task(
                    {"id": "a", "objective": "a"},
                    {
                        "id": "b",
                        "objective": "b",
                        "repeat": {
                            "back_to": "a",
                            "while_key": "k",
                            "max_iterations": 1,
                        },
                    },
                )
            )

    def test_tool_outside_tools_allowed(self) -> None:
        with pytest.raises(PlanningError, match="tools_allowed"):
            plan(
                _task(
                    {"id": "a", "objective": "a", "tools": ["shell"]},
                    tools_allowed=["sql_query"],
                )
            )

    def test_tools_are_checked_in_nested_teams(self) -> None:
        with pytest.raises(PlanningError, match="tools_allowed"):
            plan(
                _task(
                    {
                        "id": "team",
                        "objective": "t",
                        "subtasks": [{"id": "x", "objective": "x", "tools": ["rm"]}],
                    }
                )
            )

    def test_unknown_tier(self) -> None:
        with pytest.raises(PlanningError, match="tier"):
            plan(_task({"id": "a", "objective": "a", "model_tier_hint": "ultra"}))

    def test_duplicate_ids(self) -> None:
        with pytest.raises(PlanningError, match="Duplicate"):
            plan(_task({"id": "a", "objective": "a"}, {"id": "a", "objective": "b"}))

    def test_cycle_in_dependencies(self) -> None:
        with pytest.raises(PlanningError, match="cycle"):
            plan(
                _task(
                    {"id": "a", "objective": "a", "dependencies": ["b"]},
                    {"id": "b", "objective": "b", "dependencies": ["a"]},
                )
            )


class TestUseCaseDeclarations:
    """Every demo pipeline is a declaration that plans to the intended shape."""

    def test_sales_pipeline(self) -> None:
        graph = plan(analyse_ventes_alertes())
        assert ("detect_anomaly", "generate_alert", "conditional") in _edges(graph)
        assert graph.task is not None
        assert graph.task.task_constraints == TaskConstraint(
            max_cost_usd=0.50, max_latency_seconds=15.0, min_quality_score=0.85
        )

    def test_parallel_pipeline_fans_out(self) -> None:
        graph = plan(analyse_ventes_parallele())
        fan_out = {t for s, t, kind in _edges(graph) if s == "init"}
        assert fan_out == {"fetch_nord", "fetch_sud", "fetch_est"}
        assert all(kind == "parallel" for s, _, kind in _edges(graph) if s == "init")

    def test_loop_pipeline(self) -> None:
        graph = plan(redaction_rapport_iteratif())
        assert ("evaluer_critique", "rediger_brouillon", "loop") in _edges(graph)

    def test_hitl_pipeline(self) -> None:
        graph = plan(validation_budget_hitl())
        approval = {n.id for n in graph.nodes if n.interrupt_before}
        assert approval == {"validation_manager"}

    def test_supervisor_teams_are_subgraphs(self) -> None:
        graph = plan(supervision_strategique())
        teams = {n.id for n in graph.nodes if n.subgraph is not None}
        assert teams == {"research_team", "analytics_team"}
