"""Tests S6 — REST API Endpoints with FastAPI TestClient."""

import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base, get_db
from api.main import app

# Use a temporary test database
TEST_DB_PATH = "./test_ceg_api.db"
TEST_SQLALCHEMY_DATABASE_URL = f"sqlite:///{TEST_DB_PATH}"

test_engine = create_engine(
    TEST_SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True, scope="module")
def setup_test_database():
    """Create test DB tables before module tests and remove file after."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


@pytest.fixture
def client():
    """FastAPI TestClient fixture."""
    with TestClient(app) as c:
        yield c


# ── Health & Models ───────────────────────────────────────────────────────────

def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["sqlite"] == "connected"
    assert "version" in data


def test_list_models_endpoint(client):
    response = client.get("/models")
    assert response.status_code == 200
    models = response.json()
    assert isinstance(models, list)
    assert len(models) >= 3
    tiers = {m["tier"] for m in models}
    assert "fast" in tiers
    assert "balanced" in tiers
    assert "quality" in tiers


# ── Tasks CRUD ────────────────────────────────────────────────────────────────

def test_task_crud_lifecycle(client):
    # 1. Create task
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
    }
    create_res = client.post("/tasks", json=task_payload)
    assert create_res.status_code == 201
    created_data = create_res.json()
    assert created_data["id"] == "test_pipeline_task"
    assert created_data["name"] == "Pipeline de Test"

    # Duplicate creation error
    dup_res = client.post("/tasks", json=task_payload)
    assert dup_res.status_code == 400

    # 2. List tasks
    list_res = client.get("/tasks")
    assert list_res.status_code == 200
    tasks = list_res.json()
    assert any(t["id"] == "test_pipeline_task" for t in tasks)

    # 3. Get task
    get_res = client.get("/tasks/test_pipeline_task")
    assert get_res.status_code == 200
    assert get_res.json()["objective"] == "Exécuter des tests d'intégration API"

    # Get non-existent
    not_found_get = client.get("/tasks/non_existent_id")
    assert not_found_get.status_code == 404

    # 4. Update task
    update_payload = {"name": "Pipeline Mis à jour"}
    put_res = client.put("/tasks/test_pipeline_task", json=update_payload)
    assert put_res.status_code == 200
    assert put_res.json()["name"] == "Pipeline Mis à jour"

    # Update non-existent
    put_not_found = client.put("/tasks/non_existent_id", json=update_payload)
    assert put_not_found.status_code == 404

    # 5. Delete task
    del_res = client.delete("/tasks/test_pipeline_task")
    assert del_res.status_code == 204

    # Verify deleted
    get_del = client.get("/tasks/test_pipeline_task")
    assert get_del.status_code == 404

    # Delete non-existent
    del_not_found = client.delete("/tasks/non_existent_id")
    assert del_not_found.status_code == 404


# ── Execution, Trace & Metrics ────────────────────────────────────────────────

def test_execute_task_and_fetch_trace_and_metrics(client):
    # First create a task
    task_payload = {
        "id": "sales_execution_task",
        "name": "Analyse Ventes",
        "objective": "Tester l'exécution du cas d'usage fil rouge",
    }
    client.post("/tasks", json=task_payload)

    # Execute task on scenario_b
    exec_res = client.post(
        "/tasks/sales_execution_task/execute",
        json={"scenario_name": "scenario_b_single_anomaly"},
    )
    assert exec_res.status_code == 200
    exec_data = exec_res.json()
    assert exec_data["status"] == "completed"
    assert exec_data["task_id"] == "sales_execution_task"
    assert exec_data["total_cost"] > 0
    assert exec_data["total_latency_ms"] > 0
    assert "graph" in exec_data
    assert "workflow_state" in exec_data

    exec_id = exec_data["id"]

    # Execute non-existent task 404
    exec_404 = client.post("/tasks/invalid_task_id/execute", json={})
    assert exec_404.status_code == 404

    # List executions
    list_execs = client.get("/executions")
    assert list_execs.status_code == 200
    execs = list_execs.json()
    assert any(e["id"] == exec_id for e in execs)

    # Get execution detail
    detail_res = client.get(f"/executions/{exec_id}")
    assert detail_res.status_code == 200
    assert detail_res.json()["id"] == exec_id

    # Get execution 404
    detail_404 = client.get("/executions/invalid_exec_id")
    assert detail_404.status_code == 404

    # Fetch trace
    trace_res = client.get(f"/executions/{exec_id}/trace")
    assert trace_res.status_code == 200
    traces = trace_res.json()
    assert isinstance(traces, list)
    assert len(traces) >= 4  # fetch_data, aggregate_region, compute_trend, detect_anomaly, generate_alert
    node_ids = [t["node_id"] for t in traces]
    assert "fetch_data" in node_ids

    # Trace 404
    trace_404 = client.get("/executions/invalid_exec_id/trace")
    assert trace_404.status_code == 404

    # Fetch metrics
    metrics_res = client.get(f"/executions/{exec_id}/metrics")
    assert metrics_res.status_code == 200
    metrics = metrics_res.json()
    assert metrics["execution_id"] == exec_id
    assert metrics["quality_score"] > 0.0
    assert metrics["composite_score"] > 0.0

    # Metrics 404
    metrics_404 = client.get("/executions/invalid_exec_id/metrics")
    assert metrics_404.status_code == 404


# ── Benchmark Stub S6 ─────────────────────────────────────────────────────────

def test_benchmark_endpoints(client):
    bench_res = client.post("/benchmark", json={"n_runs": 5, "scenarios": ["scenario_a", "scenario_b"]})
    assert bench_res.status_code == 202
    bench_data = bench_res.json()
    assert bench_data["status"] == "ACCEPTED"
    bench_id = bench_data["id"]

    results_res = client.get(f"/benchmark/{bench_id}/results")
    assert results_res.status_code == 200
    results_data = results_res.json()
    assert results_data["id"] == bench_id

    results_404 = client.get("/benchmark/invalid_bench_id/results")
    assert results_404.status_code == 404


# ── HITL Resume Endpoint ──────────────────────────────────────────────────────

def test_resume_execution_endpoint(client):
    # 1. Create a task and execute it
    task_payload = {
        "id": "hitl_task",
        "name": "Tâche HITL",
        "objective": "Valider le point d'arrêt et de reprise",
    }
    client.post("/tasks", json=task_payload)
    exec_res = client.post("/tasks/hitl_task/execute", json={"scenario_name": "scenario_b"})
    assert exec_res.status_code == 200
    exec_id = exec_res.json()["id"]

    # 2. Resume execution with approval
    resume_payload = {
        "approved": True,
        "comment": "Validation humaine accordée",
    }
    res = client.post(f"/executions/{exec_id}/resume", json=resume_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == exec_id
    assert data["status"] == "completed"
    assert "manual_resume" in data["workflow_state"]["human_approvals"]
    assert data["workflow_state"]["human_approvals"]["manual_resume"]["approved"] is True

    # 3. Resume non-existent execution 404
    not_found = client.post("/executions/invalid_exec_id/resume", json=resume_payload)
    assert not_found.status_code == 404

