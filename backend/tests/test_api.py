"""Tests S6 — REST API Endpoints with FastAPI TestClient.

The database is a temporary file (see conftest.py) seeded with the demo
tasks on application startup.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.main import app
from ceg.runtime.decision_engine import DEFAULT_MODEL_REGISTRY

REGISTRY_MODELS = {m.name for m in DEFAULT_MODEL_REGISTRY}


@pytest.fixture
def client() -> Iterator[TestClient]:
    """FastAPI TestClient fixture (runs the startup seeding)."""
    with TestClient(app) as c:
        yield c


def _create_task(client: TestClient, **payload: Any) -> dict[str, Any]:
    res = client.post("/tasks", json=payload)
    assert res.status_code == 201, res.text
    created: dict[str, Any] = res.json()
    return created


# ── Health, Models & Pipelines ────────────────────────────────────────────────


def test_health_endpoint(client):
    from ceg import __version__

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["sqlite"] == "connected"
    assert data["version"] == __version__


def test_models_endpoint_lists_the_engine_registry(client):
    response = client.get("/models")
    assert response.status_code == 200
    models = response.json()
    assert {m["id"] for m in models} == REGISTRY_MODELS
    assert {m["tier"] for m in models} == {"fast", "balanced", "quality"}


def test_pipelines_endpoint(client):
    response = client.get("/pipelines")
    assert response.status_code == 200
    pipelines = {p["id"]: p for p in response.json()}
    assert pipelines["analyse_ventes_alertes"]["uses_csv"] is True
    assert "validation_budget_hitl" in pipelines


# ── Tasks CRUD ────────────────────────────────────────────────────────────────


def test_task_crud_lifecycle(client):
    task_payload = {
        "id": "test_pipeline_task",
        "name": "Pipeline de Test",
        "objective": "Exécuter des tests d'intégration API",
        "task_constraints": {
            "max_cost_usd": 0.25,
            "max_latency_seconds": 10.0,
            "min_quality_score": 0.80,
        },
        "tools_allowed": ["sql_query"],
        "subtasks": [
            {
                "id": "fetch_data",
                "objective": "Charger les données",
                "required_capabilities": ["reading"],
            }
        ],
        "evaluation_criteria": [
            {
                "name": "clarity",
                "description": "Sortie claire",
                "weight": 1.0,
                "evaluation_prompt_template": "{output}",
            }
        ],
    }
    created = _create_task(client, **task_payload)
    assert created["id"] == "test_pipeline_task"
    assert created["pipeline"] is None
    assert created["evaluation_criteria"][0]["name"] == "clarity"

    # Duplicate creation error
    assert client.post("/tasks", json=task_payload).status_code == 400

    # List / get
    assert any(t["id"] == "test_pipeline_task" for t in client.get("/tasks").json())
    get_res = client.get("/tasks/test_pipeline_task")
    assert get_res.status_code == 200
    assert get_res.json()["objective"] == "Exécuter des tests d'intégration API"
    assert client.get("/tasks/non_existent_id").status_code == 404

    # Update, including the evaluation criteria
    put_res = client.put(
        "/tasks/test_pipeline_task",
        json={"name": "Pipeline Mis à jour", "evaluation_criteria": []},
    )
    assert put_res.status_code == 200
    assert put_res.json()["name"] == "Pipeline Mis à jour"
    assert put_res.json()["evaluation_criteria"] == []
    assert client.put("/tasks/non_existent_id", json={"name": "x"}).status_code == 404

    # Delete
    assert client.delete("/tasks/test_pipeline_task").status_code == 204
    assert client.get("/tasks/test_pipeline_task").status_code == 404
    assert client.delete("/tasks/non_existent_id").status_code == 404


def test_unknown_pipeline_is_rejected(client):
    res = client.post("/tasks", json={"objective": "x", "pipeline": "does_not_exist"})
    assert res.status_code == 422


# ── Execution, Trace & Metrics ────────────────────────────────────────────────


def test_execute_sales_pipeline_records_real_traces(client):
    _create_task(
        client,
        id="sales_execution_task",
        objective="Tester l'exécution du cas d'usage fil rouge",
        pipeline="analyse_ventes_alertes",
    )
    exec_res = client.post(
        "/tasks/sales_execution_task/execute",
        json={"scenario_name": "scenario_b_single_anomaly", "robustness_runs": 3},
    )
    assert exec_res.status_code == 200, exec_res.text
    exec_data = exec_res.json()
    assert exec_data["status"] == "completed"
    assert exec_data["task_id"] == "sales_execution_task"
    assert exec_data["total_cost"] > 0
    exec_id = exec_data["id"]

    assert client.post("/tasks/invalid_task_id/execute", json={}).status_code == 404
    assert any(e["id"] == exec_id for e in client.get("/executions").json())
    assert client.get(f"/executions/{exec_id}").json()["id"] == exec_id
    assert client.get("/executions/invalid_exec_id").status_code == 404

    traces = {
        t["node_id"]: t for t in client.get(f"/executions/{exec_id}/trace").json()
    }
    assert set(traces) == {
        "fetch_data",
        "aggregate_region",
        "compute_trend",
        "detect_anomaly",
        "generate_alert",
    }
    # The model really chosen by the engine is recorded, per the tier hints.
    assert {t["model"] for t in traces.values()} <= REGISTRY_MODELS
    assert traces["detect_anomaly"]["model"] == "quality-pro"
    assert traces["detect_anomaly"]["decision"]["tier_hint"] == "quality"
    assert traces["fetch_data"]["prompt"]["objective"]
    assert client.get("/executions/invalid_exec_id/trace").status_code == 404

    metrics = client.get(f"/executions/{exec_id}/metrics").json()
    assert metrics["execution_id"] == exec_id
    assert metrics["quality_score"] is not None
    assert metrics["robustness_score"] == 1.0  # 3 runs, all succeeded
    assert metrics["composite_score"] > 0.0
    assert metrics["report"]["metadata"]["judge"] == "MockJudgeClient"
    assert client.get("/executions/invalid_exec_id/metrics").status_code == 404


def test_robustness_is_unmeasured_by_default(client):
    exec_res = client.post(
        "/tasks/analyse_ventes_alertes/execute",
        json={"scenario_name": "scenario_a_normal"},
    )
    metrics = client.get(f"/executions/{exec_res.json()['id']}/metrics").json()
    assert metrics["robustness_score"] is None
    assert "robustness" in metrics["report"]["metadata"]["unmeasured"]


def test_failed_execution_traces_the_real_failing_node(client):
    exec_res = client.post(
        "/tasks/analyse_ventes_alertes/execute",
        json={"scenario_name": "scenario_d_corrupted"},
    )
    assert exec_res.status_code == 200
    data = exec_res.json()
    assert data["status"] == "failed"
    assert "fetch_data" in data["error"]

    traces = client.get(f"/executions/{data['id']}/trace").json()
    failed = [t for t in traces if t["status"] == "failed"]
    assert [t["node_id"] for t in failed] == ["fetch_data"]
    assert failed[0]["fallbacks_triggered"] == ["retry", "escalation", "abort"]
    assert failed[0]["tokens_input"] == 0  # nothing invented

    metrics = client.get(f"/executions/{data['id']}/metrics").json()
    assert metrics["composite_score"] == 0.0


def test_csv_path_outside_data_dir_is_refused(client):
    res = client.post(
        "/tasks/analyse_ventes_alertes/execute",
        json={"csv_path": "/etc/passwd"},
    )
    assert res.status_code == 400
    # Nothing was executed nor stored for the refused request.
    assert all(e["status"] != "running" for e in client.get("/executions").json())


def test_task_without_pipeline_runs_its_subtasks_with_inputs(client):
    _create_task(
        client,
        id="generic_task",
        objective="Tâche générique",
        subtasks=[
            {"id": "step_1", "objective": "Première étape"},
            {
                "id": "step_2",
                "objective": "Deuxième étape",
                "model_tier_hint": "quality",
                "dependencies": ["step_1"],
            },
        ],
    )
    res = client.post(
        "/tasks/generic_task/execute", json={"inputs": {"customer": "ACME"}}
    )
    assert res.status_code == 200, res.text
    state = res.json()["workflow_state"]
    assert set(state["node_statuses"]) == {"step_1", "step_2"}
    # Graph inputs reach the executors (MockExecutor echoes the input keys).
    assert "customer" in state["node_outputs"]["step_1"]["input_keys"]
    traces = {
        t["node_id"]: t
        for t in client.get(f"/executions/{res.json()['id']}/trace").json()
    }
    assert traces["step_2"]["model"] == "quality-pro"


# ── Benchmark Stub S6 ─────────────────────────────────────────────────────────


def test_benchmark_endpoints(client):
    bench_res = client.post(
        "/benchmark", json={"n_runs": 5, "scenarios": ["scenario_a", "scenario_b"]}
    )
    assert bench_res.status_code == 202
    bench_id = bench_res.json()["id"]
    assert client.get(f"/benchmark/{bench_id}/results").json()["id"] == bench_id
    assert client.get("/benchmark/invalid_bench_id/results").status_code == 404


# ── Human-in-the-Loop ─────────────────────────────────────────────────────────


def _start_hitl(client: TestClient) -> str:
    res = client.post("/tasks/validation_budget_hitl/execute", json={})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "awaiting_approval"
    pending = data["workflow_state"]["pending_approvals"]
    assert pending[0]["node_id"] == "validation_manager"
    # Nothing after the approval node ran yet.
    assert "decaisser_fonds" not in data["workflow_state"]["node_statuses"]
    exec_id: str = data["id"]
    return exec_id


def test_hitl_execution_pauses_and_resumes_on_approval(client):
    exec_id = _start_hitl(client)

    res = client.post(
        f"/executions/{exec_id}/resume",
        json={"approved": True, "comment": "Validation humaine accordée"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "completed"
    statuses = data["workflow_state"]["node_statuses"]
    assert statuses["validation_manager"] == "completed"
    assert statuses["decaisser_fonds"] == "completed"
    approval = data["workflow_state"]["human_approvals"]["validation_manager"]
    assert approval["before"]["comment"] == "Validation humaine accordée"

    # Already completed: it cannot be resumed again.
    again = client.post(f"/executions/{exec_id}/resume", json={"approved": True})
    assert again.status_code == 409


def test_hitl_rejection_skips_the_node(client):
    exec_id = _start_hitl(client)
    res = client.post(f"/executions/{exec_id}/resume", json={"approved": False})
    assert res.status_code == 200
    statuses = res.json()["workflow_state"]["node_statuses"]
    assert statuses["validation_manager"] == "skipped"


def test_resume_errors(client):
    exec_res = client.post(
        "/tasks/analyse_ventes_alertes/execute",
        json={"scenario_name": "scenario_a_normal"},
    )
    completed_id = exec_res.json()["id"]
    assert client.post(f"/executions/{completed_id}/resume", json={}).status_code == 409
    assert client.post("/executions/invalid_exec_id/resume", json={}).status_code == 404


# ── Backends, declarations and constraints ────────────────────────────────────


def test_backends_endpoint(client: TestClient) -> None:
    backends = {b["id"]: b for b in client.get("/backends").json()}
    assert backends["langgraph"]["supports_hitl"] is True
    assert backends["python"]["supports_hitl"] is False


def test_same_task_runs_identically_on_both_backends(client: TestClient) -> None:
    runs = {}
    for backend in ("langgraph", "python"):
        res = client.post(
            "/tasks/analyse_ventes_alertes/execute",
            json={"scenario_name": "scenario_b_single_anomaly", "backend": backend},
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["backend"] == backend
        traces = client.get(f"/executions/{data['id']}/trace").json()
        runs[backend] = (
            data["status"],
            data["total_cost"],
            sorted((t["node_id"], t["model"]) for t in traces),
        )
    assert runs["langgraph"] == runs["python"]


def test_hitl_task_on_a_backend_without_hitl_is_refused(client: TestClient) -> None:
    before = len(client.get("/executions").json())
    res = client.post(
        "/tasks/validation_budget_hitl/execute", json={"backend": "python"}
    )
    assert res.status_code == 400
    assert "human approval" in res.json()["detail"]
    assert len(client.get("/executions").json()) == before


def test_unknown_backend_is_refused(client: TestClient) -> None:
    res = client.post("/tasks/analyse_ventes_alertes/execute", json={"backend": "x"})
    assert res.status_code == 400


def test_invalid_declarations_are_refused(client: TestClient) -> None:
    res = client.post(
        "/tasks",
        json={
            "objective": "o",
            "tools_allowed": ["sql_query"],
            "subtasks": [{"id": "a", "objective": "a", "tools": ["shell"]}],
        },
    )
    assert res.status_code == 422
    assert "tools_allowed" in res.json()["detail"]

    _create_task(
        client,
        id="declared_task",
        objective="o",
        subtasks=[{"id": "a", "objective": "a"}],
    )
    bad_update = client.put(
        "/tasks/declared_task",
        json={"subtasks": [{"id": "b", "objective": "b", "run_if": "ghost.ok"}]},
    )
    assert bad_update.status_code == 422
    # The stored declaration is unchanged.
    subtasks = client.get("/tasks/declared_task").json()["subtasks"]
    assert [st["id"] for st in subtasks] == ["a"]


def test_quality_below_declared_minimum_fails_the_execution(
    client: TestClient,
) -> None:
    _create_task(
        client,
        id="demanding_task",
        objective="Tâche exigeante",
        task_constraints={"min_quality_score": 0.95},
        subtasks=[{"id": "write", "objective": "Rédiger"}],
        evaluation_criteria=[
            {
                "name": "clarity",
                "description": "Sortie claire",
                "weight": 1.0,
                "evaluation_prompt_template": "{output}",
            }
        ],
    )
    res = client.post("/tasks/demanding_task/execute", json={})
    data = res.json()
    # MockJudgeClient scores 0.90 < 0.95.
    assert data["status"] == "failed"
    assert "min_quality_score" in data["error"]
    report = client.get(f"/executions/{data['id']}/metrics").json()["report"]
    assert report["constraint_violations"]
