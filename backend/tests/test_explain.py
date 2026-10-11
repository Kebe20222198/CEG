"""EXPLAIN: a plan's minimum cost and latency, checked before running."""

from __future__ import annotations

import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from ceg.cli import main
from ceg.explain import explain
from ceg.models.task import CognitiveTask, SubTask, TaskConstraint
from ceg.planner import plan
from ceg.use_cases.multi_agent_supervisor import build_hierarchical_supervisor_graph
from ceg.use_cases.sales_pipeline import build_sales_graph


def _chain(
    *,
    budget: float = 1.0,
    latency: float = 60.0,
    quality: float = 0.0,
    tier: str | None = None,
    n: int = 3,
) -> CognitiveTask:
    return CognitiveTask(
        objective="chaîne",
        task_constraints=TaskConstraint(
            max_cost_usd=budget,
            max_latency_seconds=latency,
            min_quality_score=quality,
        ),
        subtasks=[
            SubTask(
                id=f"s{i}",
                objective=f"étape {i}",
                required_capabilities=["anomaly_detection", "reasoning"],
                model_tier_hint=tier,
                dependencies=[f"s{i - 1}"] if i else [],
            )
            for i in range(n)
        ],
    )


class TestBounds:
    def test_cheapest_model_per_step_without_floor(self) -> None:
        estimate = explain(plan(_chain()))
        assert [s.model for s in estimate.steps] == ["fast-mini"] * 3
        assert estimate.min_cost == pytest.approx(0.006)
        assert estimate.min_latency_ms == pytest.approx(240.0)
        assert estimate.feasible and not estimate.warnings

    def test_the_quality_floor_raises_the_minimum(self) -> None:
        # Only balanced and quality models are rated ≥ 0.75 on analysis.
        estimate = explain(plan(_chain(quality=0.75)))
        assert {s.model for s in estimate.steps} == {"balanced-standard"}
        assert estimate.min_cost == pytest.approx(0.036)

    def test_a_step_behind_a_condition_is_not_in_the_bounds(self) -> None:
        estimate = explain(build_sales_graph())
        alert = next(s for s in estimate.steps if s.node_id == "generate_alert")
        assert alert.optional
        mandatory = sum(s.cost for s in estimate.steps if not s.optional)
        assert estimate.min_cost == pytest.approx(mandatory)

    def test_a_team_counts_its_sub_graph(self) -> None:
        estimate = explain(build_hierarchical_supervisor_graph())
        team = next(s for s in estimate.steps if s.node_id == "research_team")
        members = [s for s in estimate.steps if s.node_id.startswith("research_team/")]
        assert team.team and members
        assert team.cost == pytest.approx(
            sum(s.cost for s in members if not s.optional)
        )


class TestVerdict:
    def test_budget_below_the_minimum_is_refused(self) -> None:
        estimate = explain(plan(_chain(budget=0.005)))
        assert not estimate.feasible
        assert "Budget insuffisant" in estimate.errors[0]

    def test_latency_below_the_minimum_is_refused(self) -> None:
        estimate = explain(plan(_chain(latency=0.2)))
        assert not estimate.feasible
        assert "Latence insuffisante" in estimate.errors[0]

    def test_a_tier_hint_against_the_floor_is_a_warning(self) -> None:
        estimate = explain(plan(_chain(quality=0.75, tier="fast")))
        assert estimate.feasible
        assert len(estimate.warnings) == 3
        assert "l'indication « fast »" in estimate.warnings[0]

    def test_an_unreachable_floor_is_a_warning(self) -> None:
        estimate = explain(plan(_chain(quality=0.99)))
        assert estimate.feasible
        assert "aucun modèle n'atteint la qualité 0.99" in estimate.warnings[0]


IMPOSSIBLE = textwrap.dedent(
    '''
    from ceg import CognitiveTask, SubTask, TaskConstraint, workflow

    @workflow
    def trop_court() -> CognitiveTask:
        """Trois étapes d'analyse pour un millième de dollar."""
        return CognitiveTask(
            objective="o",
            task_constraints=TaskConstraint(max_cost_usd=0.001),
            subtasks=[
                SubTask(id=f"s{i}", objective="analyser",
                        required_capabilities=["reasoning"])
                for i in range(3)
            ],
        )
    '''
)


class TestCli:
    def _run(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, command: str
    ) -> tuple[int, str]:
        (tmp_path / "trop_court.py").write_text(IMPOSSIBLE, encoding="utf-8")
        code = main([command, "trop_court", "--workflows", str(tmp_path)])
        return code, capsys.readouterr().out

    def test_validate_refuses_an_infeasible_plan(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        code, out = self._run(capsys, tmp_path, "validate")
        assert code == 1
        assert "EXPLAIN" in out and "Budget insuffisant" in out

    def test_run_refuses_before_running(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        code, out = self._run(capsys, tmp_path, "run")
        assert code == 1
        assert "Plan refusé avant exécution" in out
        assert "Total" not in out  # nothing ran


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def test_workflow_detail_includes_the_estimate(client: TestClient) -> None:
    detail = client.get("/workflows/analyse_ventes_alertes").json()
    estimate = detail["estimate"]
    assert estimate["feasible"]
    assert estimate["min_cost"] == pytest.approx(0.076)
    assert {s["node_id"] for s in estimate["steps"]} >= {"fetch_data", "detect_anomaly"}
    assert any("fetch_data" in w for w in estimate["warnings"])
