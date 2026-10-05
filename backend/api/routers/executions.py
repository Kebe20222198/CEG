"""Executions, Traces, and Metrics router."""

import json

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
from api.services import ExecutionStateError, to_detail, to_response
from api.services import resume_execution as resume

router = APIRouter(tags=["Executions"])


@router.get(
    "/executions",
    response_model=list[ExecutionResponse],
    summary="Lister les exécutions",
)
def list_executions(
    status_filter: str | None = Query(
        None,
        alias="status",
        description=(
            "Filtrer par statut (running, awaiting_approval, completed, failed)"
        ),
    ),
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
    return [to_response(r) for r in records]


@router.get(
    "/executions/{id}",
    response_model=ExecutionDetailResponse,
    summary="Détail d'une exécution",
)
def get_execution(id: str, db: Session = Depends(get_db)) -> ExecutionDetailResponse:
    """Récupérer le détail complet d'une exécution (graphe + état de workflow)."""
    return to_detail(_get_execution_or_404(db, id))


@router.get(
    "/executions/{id}/trace",
    response_model=list[TraceItemResponse],
    summary="Trace complète d'une exécution",
)
def get_execution_trace(
    id: str, db: Session = Depends(get_db)
) -> list[TraceItemResponse]:
    """Récupérer la trace d'exécution nœud par nœud pour une exécution donnée."""
    _get_execution_or_404(db, id)

    traces = (
        db.query(TraceModel)
        .filter(TraceModel.execution_id == id)
        .order_by(TraceModel.id.asc())
        .all()
    )

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
            fallbacks_triggered=json.loads(t.fallbacks_triggered_json)
            if t.fallbacks_triggered_json
            else None,
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
    payload: ResumeExecutionRequest | None = None,
    db: Session = Depends(get_db),
) -> ExecutionDetailResponse:
    """Reprendre une exécution mise en pause par un nœud Human-in-the-Loop.

    ``approved=false`` rejette l'action : le nœud en attente est marqué
    ``skipped`` et le graphe continue. Répond 409 si l'exécution n'est pas en
    attente d'approbation.
    """
    record = _get_execution_or_404(db, id)
    try:
        resume(db, record, payload or ResumeExecutionRequest())
    except ExecutionStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return to_detail(record)


def _get_execution_or_404(db: Session, execution_id: str) -> ExecutionModel:
    record = db.get(ExecutionModel, execution_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exécution '{execution_id}' non trouvée.",
        )
    return record
