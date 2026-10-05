"""Executions, Traces, and Metrics router."""

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.db import ExecutionModel, MetricModel, TraceModel, get_db
from api.schemas import (
    ExecutionDetailResponse,
    ExecutionResponse,
    MetricsResponse,
    ResumeExecutionRequest,
    TraceItemResponse,
)

router = APIRouter(tags=["Executions"])


@router.get("/executions", response_model=list[ExecutionResponse], summary="Lister les exécutions")
def list_executions(
    status_filter: str | None = Query(None, alias="status", description="Filtrer par statut (completed, failed, running, pending)"),
    task_id: str | None = Query(None, description="Filtrer par task_id"),
    db: Session = Depends(get_db),
) -> list[ExecutionResponse]:
    """Lister toutes les exécutions enregistrées avec filtres optionnels."""
    query = db.query(ExecutionModel)

    if status_filter:
        query = query.filter(ExecutionModel.status == status_filter)
    if task_id:
        query = query.filter(ExecutionModel.task_id == task_id)

    records = query.order_by(ExecutionModel.started_at.desc()).all()

    return [
        ExecutionResponse(
            id=r.id,
            task_id=r.task_id,
            scenario_name=r.scenario_name,
            status=r.status,
            started_at=r.started_at.isoformat() if r.started_at else None,
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            total_cost=r.total_cost,
            total_latency_ms=r.total_latency_ms,
            summary=r.summary,
            error=r.error,
        )
        for r in records
    ]


@router.get(
    "/executions/{id}",
    response_model=ExecutionDetailResponse,
    summary="Détail d'une exécution",
)
def get_execution(id: str, db: Session = Depends(get_db)) -> ExecutionDetailResponse:
    """Récupérer le détail complet d'une exécution (graphe + état de workflow)."""
    r = db.query(ExecutionModel).filter(ExecutionModel.id == id).first()
    if not r:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exécution '{id}' non trouvée.",
        )

    graph = json.loads(r.graph_json) if r.graph_json else None
    workflow_state = json.loads(r.workflow_state_json) if r.workflow_state_json else None

    return ExecutionDetailResponse(
        id=r.id,
        task_id=r.task_id,
        scenario_name=r.scenario_name,
        status=r.status,
        started_at=r.started_at.isoformat() if r.started_at else None,
        completed_at=r.completed_at.isoformat() if r.completed_at else None,
        total_cost=r.total_cost,
        total_latency_ms=r.total_latency_ms,
        summary=r.summary,
        error=r.error,
        graph=graph,
        workflow_state=workflow_state,
    )


@router.get(
    "/executions/{id}/trace",
    response_model=list[TraceItemResponse],
    summary="Trace complète d'une exécution",
)
def get_execution_trace(id: str, db: Session = Depends(get_db)) -> list[TraceItemResponse]:
    """Récupérer la trace d'exécution nœud par nœud pour une exécution donnée."""
    exec_exists = db.query(ExecutionModel).filter(ExecutionModel.id == id).first()
    if not exec_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exécution '{id}' non trouvée.",
        )

    traces = db.query(TraceModel).filter(TraceModel.execution_id == id).order_by(TraceModel.id.asc()).all()

    return [
        TraceItemResponse(
            node_id=t.node_id,
            status=t.status,
            model=t.model,
            prompt=json.loads(t.prompt_json) if t.prompt_json else None,
            response=json.loads(t.response_json) if t.response_json else None,
            tokens_input=t.tokens_input,
            tokens_output=t.tokens_output,
            cost=t.cost,
            latency_ms=t.latency_ms,
            decision=json.loads(t.decision_json) if t.decision_json else None,
            fallbacks_triggered=json.loads(t.fallbacks_triggered_json) if t.fallbacks_triggered_json else None,
            error=t.error,
        )
        for t in traces
    ]


@router.get(
    "/executions/{id}/metrics",
    response_model=MetricsResponse,
    summary="Métriques d'exécution",
)
def get_execution_metrics(id: str, db: Session = Depends(get_db)) -> MetricsResponse:
    """Récupérer le rapport de métriques d'évaluation pour une exécution."""
    m = db.query(MetricModel).filter(MetricModel.execution_id == id).first()
    if not m:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Métriques non trouvées pour l'exécution '{id}'.",
        )

    report = json.loads(m.report_json) if m.report_json else {}

    return MetricsResponse(
        execution_id=m.execution_id,
        cost_usd=m.cost_usd,
        latency_ms=m.latency_ms,
        quality_score=m.quality_score,
        robustness_score=m.robustness_score,
        composite_score=m.composite_score,
        report=report,
    )


@router.post(
    "/executions/{id}/resume",
    response_model=ExecutionDetailResponse,
    summary="Reprendre une exécution en pause (Human-in-the-Loop)",
)
def resume_execution(
    id: str,
    payload: ResumeExecutionRequest = ResumeExecutionRequest(),
    db: Session = Depends(get_db),
) -> ExecutionDetailResponse:
    """Reprendre une exécution mise en pause par un nœud Human-in-the-Loop."""
    r = db.query(ExecutionModel).filter(ExecutionModel.id == id).first()
    if not r:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exécution '{id}' non trouvée.",
        )

    # Update approval records in workflow state
    workflow_state = json.loads(r.workflow_state_json) if r.workflow_state_json else {}
    approvals = workflow_state.get("human_approvals", {})
    approvals["manual_resume"] = {
        "approved": payload.approved,
        "value": payload.value,
        "comment": payload.comment,
        "resumed_at": datetime.now(timezone.utc).isoformat(),
    }
    workflow_state["human_approvals"] = approvals

    r.status = "completed" if payload.approved else "cancelled"
    r.completed_at = datetime.now(timezone.utc)
    decision_str = "approuvée" if payload.approved else "rejetée"
    r.summary = f"Reprise HITL ({decision_str}) : {payload.comment or 'Sans commentaire'}"
    r.workflow_state_json = json.dumps(workflow_state)
    db.commit()
    db.refresh(r)

    graph = json.loads(r.graph_json) if r.graph_json else None
    return ExecutionDetailResponse(
        id=r.id,
        task_id=r.task_id,
        scenario_name=r.scenario_name,
        status=r.status,
        started_at=r.started_at.isoformat() if r.started_at else None,
        completed_at=r.completed_at.isoformat() if r.completed_at else None,
        total_cost=r.total_cost,
        total_latency_ms=r.total_latency_ms,
        summary=r.summary,
        error=r.error,
        graph=graph,
        workflow_state=workflow_state,
    )
