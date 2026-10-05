"""CognitiveTask CRUD and Execution router."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.db import ExecutionModel, MetricModel, TaskModel, TraceModel, get_db
from api.schemas import (
    ExecuteTaskRequest,
    ExecutionDetailResponse,
    TaskCreate,
    TaskResponse,
    TaskUpdate,
)
from ceg.compiler.compiler import CEGCompiler
from ceg.evaluation.engine import EvaluationEngine
from ceg.models.task import CognitiveTask, SubTask, TaskConstraint
from ceg.use_cases.sales_criteria import ALL_SALES_CRITERIA
from ceg.use_cases.sales_pipeline import SalesExecutor, build_sales_graph
from ceg.use_cases.demo_pipelines import (
    build_parallel_sales_graph,
    ParallelSalesExecutor,
    build_loop_report_graph,
    LoopReportExecutor,
    build_hitl_budget_graph,
    HITLBudgetExecutor,
)
from ceg.use_cases.multi_agent_supervisor import (
    build_hierarchical_supervisor_graph,
    HierarchicalSupervisorExecutor,
)

router = APIRouter(tags=["Tasks"])

FIXTURES_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures"


def _model_to_response(model: TaskModel) -> TaskResponse:
    """Convert SQLAlchemy TaskModel to TaskResponse Pydantic schema."""
    task_constraints = None
    if model.task_constraints_json:
        try:
            task_constraints = TaskConstraint.model_validate_json(model.task_constraints_json)
        except Exception:
            pass

    subtasks = []
    if model.subtasks_json:
        try:
            subtasks = [SubTask.model_validate(st) for st in json.loads(model.subtasks_json)]
        except Exception:
            pass

    tools_allowed = json.loads(model.tools_allowed_json) if model.tools_allowed_json else []
    evaluation_criteria = json.loads(model.evaluation_criteria_json) if model.evaluation_criteria_json else []

    return TaskResponse(
        id=model.id,
        name=model.name,
        objective=model.objective,
        task_constraints=task_constraints,
        tools_allowed=tools_allowed,
        subtasks=subtasks,
        evaluation_criteria=evaluation_criteria,
        created_at=model.created_at.isoformat() if model.created_at else "",
        updated_at=model.updated_at.isoformat() if model.updated_at else "",
    )


@router.post(
    "/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une Cognitive Task",
)
def create_task(
    payload: TaskCreate,
    db: Session = Depends(get_db),
) -> TaskResponse:
    """Créer une nouvelle tâche cognitive et la persister en base."""
    task_id = payload.id or f"task_{uuid.uuid4().hex[:8]}"

    # Check for duplicates
    existing = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La tâche '{task_id}' existe déjà.",
        )

    task_constraints_json = (
        payload.task_constraints.model_dump_json() if payload.task_constraints else None
    )
    tools_json = json.dumps(payload.tools_allowed)
    subtasks_json = json.dumps([st.model_dump() for st in payload.subtasks])
    eval_criteria_json = json.dumps(payload.evaluation_criteria)

    now = datetime.now(timezone.utc)
    model = TaskModel(
        id=task_id,
        name=payload.name or task_id,
        objective=payload.objective,
        task_constraints_json=task_constraints_json,
        tools_allowed_json=tools_json,
        subtasks_json=subtasks_json,
        evaluation_criteria_json=eval_criteria_json,
        created_at=now,
        updated_at=now,
    )
    db.add(model)
    db.commit()
    db.refresh(model)

    return _model_to_response(model)


@router.get("/tasks", response_model=list[TaskResponse], summary="Lister les Cognitive Tasks")
def list_tasks(db: Session = Depends(get_db)) -> list[TaskResponse]:
    """Lister toutes les Cognitive Tasks enregistrées."""
    models = db.query(TaskModel).order_by(TaskModel.created_at.desc()).all()
    return [_model_to_response(m) for m in models]


@router.get("/tasks/{id}", response_model=TaskResponse, summary="Récupérer une Cognitive Task")
def get_task(id: str, db: Session = Depends(get_db)) -> TaskResponse:
    """Récupérer le détail d'une tâche cognitive par son identifiant."""
    model = db.query(TaskModel).filter(TaskModel.id == id).first()
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tâche '{id}' non trouvée.",
        )
    return _model_to_response(model)


@router.put("/tasks/{id}", response_model=TaskResponse, summary="Mettre à jour une Cognitive Task")
def update_task(
    id: str,
    payload: TaskUpdate,
    db: Session = Depends(get_db),
) -> TaskResponse:
    """Mettre à jour les champs d'une tâche cognitive."""
    model = db.query(TaskModel).filter(TaskModel.id == id).first()
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tâche '{id}' non trouvée.",
        )

    if payload.name is not None:
        model.name = payload.name
    if payload.objective is not None:
        model.objective = payload.objective
    if payload.task_constraints is not None:
        model.task_constraints_json = payload.task_constraints.model_dump_json()
    if payload.tools_allowed is not None:
        model.tools_allowed_json = json.dumps(payload.tools_allowed)
    if payload.subtasks is not None:
        model.subtasks_json = json.dumps([st.model_dump() for st in payload.subtasks])

    model.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(model)

    return _model_to_response(model)


@router.delete("/tasks/{id}", status_code=status.HTTP_204_NO_CONTENT, summary="Supprimer une Cognitive Task")
def delete_task(id: str, db: Session = Depends(get_db)) -> None:
    """Supprimer une tâche cognitive de la base de données."""
    model = db.query(TaskModel).filter(TaskModel.id == id).first()
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tâche '{id}' non trouvée.",
        )
    db.delete(model)
    db.commit()


@router.post(
    "/tasks/{id}/execute",
    response_model=ExecutionDetailResponse,
    summary="Exécuter une Cognitive Task",
)
def execute_task(
    id: str,
    payload: ExecuteTaskRequest = ExecuteTaskRequest(),
    db: Session = Depends(get_db),
) -> ExecutionDetailResponse:
    """Exécuter une tâche cognitive en compilant le graphe et en évaluant les métriques."""
    task_model = db.query(TaskModel).filter(TaskModel.id == id).first()
    if not task_model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tâche '{id}' non trouvée.",
        )

    # Determine CSV file path for sales pipeline execution
    scenario = payload.scenario_name.lower()
    if payload.csv_path:
        csv_path = payload.csv_path
    elif "scenario_a" in scenario or "normal" in scenario:
        csv_path = str(FIXTURES_DIR / "scenario_a_normal.csv")
    elif "scenario_c" in scenario or "multi" in scenario:
        csv_path = str(FIXTURES_DIR / "scenario_c_multiple_anomalies.csv")
    elif "scenario_d" in scenario or "corrupted" in scenario or "fault" in scenario:
        csv_path = str(FIXTURES_DIR / "scenario_d_corrupted.csv")
    else:
        # Default scenario_b
        csv_path = str(FIXTURES_DIR / "scenario_b_single_anomaly.csv")

    exec_id = f"exec_{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc)

    if task_model.id == "multi_agent_supervisor":
        graph = build_hierarchical_supervisor_graph()
        executor = HierarchicalSupervisorExecutor()
    elif task_model.id == "analyse_ventes_parallele":
        graph = build_parallel_sales_graph()
        executor = ParallelSalesExecutor()
    elif task_model.id == "redaction_rapport_iteratif":
        graph = build_loop_report_graph()
        executor = LoopReportExecutor()
    elif task_model.id == "validation_budget_hitl":
        graph = build_hitl_budget_graph()
        executor = HITLBudgetExecutor()
    else:
        graph = build_sales_graph()
        executor = SalesExecutor(csv_path=csv_path)

    graph_dict = graph.model_dump()

    exec_record = ExecutionModel(
        id=exec_id,
        task_id=task_model.id,
        scenario_name=payload.scenario_name,
        status="running",
        started_at=started_at,
        graph_json=json.dumps(graph_dict),
    )
    db.add(exec_record)
    db.commit()

    compiler = CEGCompiler(executor=executor)
    workflow = compiler.compile(graph)

    workflow_state: dict[str, Any] = {}
    err_msg: str | None = None
    exec_status = "completed"

    try:
        workflow_state = workflow.invoke(payload.inputs)
    except Exception as exc:
        exec_status = "failed"
        err_msg = str(exc)
        workflow_state = {
            "total_cost": 0.0,
            "total_latency_ms": 0.0,
            "execution_log": [
                {
                    "node_id": "fetch_data",
                    "status": "failed",
                    "model": "mock-fast",
                    "prompt": {"csv_path": csv_path},
                    "response": None,
                    "tokens": {"input": 120, "output": 0},
                    "cost": 0.0001,
                    "latency_ms": 15.0,
                    "decision": "Route to fetch_data (attempt 1)",
                    "fallbacks_triggered": ["switch_model", "retry_same", "abort"],
                    "error": err_msg,
                }
            ],
        }

    completed_at = datetime.now(timezone.utc)
    total_cost = float(workflow_state.get("total_cost", 0.0))
    total_latency_ms = float(workflow_state.get("total_latency_ms", 0.0))

    # Evaluate using EvaluationEngine
    engine = EvaluationEngine(criteria=ALL_SALES_CRITERIA)
    report = engine.evaluate(
        workflow_state=workflow_state,
        scenario_name=payload.scenario_name,
        max_budget_usd=0.50,
        max_latency_seconds=15.0,
    )
    if exec_status == "failed":
        report.robustness_score = 0.0
        report.quality_score = 0.0
        report.composite_score = 0.0

    # Summary generator
    summary = f"Scénario {payload.scenario_name}: {exec_status}. Coût: ${total_cost:.4f}, Latence: {total_latency_ms:.1f}ms."

    # Update Execution in DB
    exec_record.status = exec_status
    exec_record.completed_at = completed_at
    exec_record.total_cost = total_cost
    exec_record.total_latency_ms = total_latency_ms
    exec_record.workflow_state_json = json.dumps(workflow_state)
    exec_record.summary = summary
    exec_record.error = err_msg

    # Insert Traces
    execution_log = workflow_state.get("execution_log", [])
    for entry in execution_log:
        t_model = TraceModel(
            execution_id=exec_id,
            node_id=entry.get("node_id", "unknown"),
            status=entry.get("status", "completed"),
            model=entry.get("model", "mock-fast"),
            prompt_json=json.dumps(entry.get("prompt")),
            response_json=json.dumps(entry.get("response")),
            tokens_input=entry.get("tokens", {}).get("input", 0) if isinstance(entry.get("tokens"), dict) else 0,
            tokens_output=entry.get("tokens", {}).get("output", 0) if isinstance(entry.get("tokens"), dict) else 0,
            cost=float(entry.get("cost", 0.0)),
            latency_ms=float(entry.get("latency_ms", 0.0)),
            decision_json=json.dumps(entry.get("decision")),
            fallbacks_triggered_json=json.dumps(entry.get("fallbacks_triggered")),
            error=entry.get("error"),
        )
        db.add(t_model)

    # Insert Metric
    metric_record = MetricModel(
        id=exec_id,
        execution_id=exec_id,
        cost_usd=report.total_cost_usd,
        latency_ms=report.total_latency_ms,
        quality_score=report.quality_score,
        robustness_score=report.robustness_score,
        composite_score=report.composite_score,
        report_json=report.model_dump_json(),
    )
    db.add(metric_record)
    db.commit()

    return ExecutionDetailResponse(
        id=exec_id,
        task_id=task_model.id,
        scenario_name=payload.scenario_name,
        status=exec_status,
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        total_cost=total_cost,
        total_latency_ms=total_latency_ms,
        summary=summary,
        error=err_msg,
        graph=graph_dict,
        workflow_state=workflow_state,
    )
