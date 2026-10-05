"""CognitiveTask CRUD and Execution router."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.db import TaskModel, get_db
from api.pipelines import PIPELINES
from api.schemas import (
    ExecuteTaskRequest,
    ExecutionDetailResponse,
    TaskCreate,
    TaskResponse,
    TaskUpdate,
)
from api.services import (
    ExecutionRequestError,
    cognitive_task_of,
    start_execution,
    to_detail,
)
from ceg.evaluation.models import Criterion
from ceg.models.task import SubTask, TaskConstraint
from ceg.planner import PlanningError, plan

router = APIRouter(tags=["Tasks"])


def _model_to_response(model: TaskModel) -> TaskResponse:
    """Convert SQLAlchemy TaskModel to TaskResponse Pydantic schema."""
    task_constraints = (
        TaskConstraint.model_validate_json(model.task_constraints_json)
        if model.task_constraints_json
        else None
    )
    subtasks = [SubTask.model_validate(st) for st in json.loads(model.subtasks_json)]
    criteria = [
        Criterion.model_validate(c) for c in json.loads(model.evaluation_criteria_json)
    ]
    return TaskResponse(
        id=model.id,
        name=model.name,
        objective=model.objective,
        pipeline=model.pipeline,
        task_constraints=task_constraints,
        tools_allowed=json.loads(model.tools_allowed_json),
        subtasks=subtasks,
        evaluation_criteria=criteria,
        created_at=model.created_at.isoformat() if model.created_at else "",
        updated_at=model.updated_at.isoformat() if model.updated_at else "",
    )


def _get_task_or_404(db: Session, task_id: str) -> TaskModel:
    model = db.get(TaskModel, task_id)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tâche '{task_id}' non trouvée.",
        )
    return model


def _check_pipeline(pipeline: str | None) -> None:
    if pipeline is not None and pipeline not in PIPELINES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Pipeline inconnu '{pipeline}'. Disponibles : {sorted(PIPELINES)}."
            ),
        )


def _check_declaration(db: Session, model: TaskModel) -> None:
    """Refuse a task whose declaration cannot be planned (HTTP 422).

    Unknown dependencies, inconsistent conditions or tools outside
    ``tools_allowed`` are rejected when the task is written, not when it runs.
    """
    try:
        plan(cognitive_task_of(model))
    except PlanningError as exc:
        db.rollback()
        raise HTTPException(
            status_code=422, detail=f"Déclaration invalide : {exc}"
        ) from exc


def _commit(db: Session) -> None:
    """Commit, leaving the session usable if the commit fails."""
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


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
    if db.get(TaskModel, task_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La tâche '{task_id}' existe déjà.",
        )
    _check_pipeline(payload.pipeline)

    now = datetime.now(timezone.utc)
    model = TaskModel(
        id=task_id,
        name=payload.name or task_id,
        objective=payload.objective,
        pipeline=payload.pipeline,
        task_constraints_json=(
            payload.task_constraints.model_dump_json()
            if payload.task_constraints
            else None
        ),
        tools_allowed_json=json.dumps(payload.tools_allowed),
        subtasks_json=json.dumps([st.model_dump() for st in payload.subtasks]),
        evaluation_criteria_json=json.dumps(
            [c.model_dump() for c in payload.evaluation_criteria]
        ),
        created_at=now,
        updated_at=now,
    )
    _check_declaration(db, model)
    db.add(model)
    _commit(db)
    db.refresh(model)
    return _model_to_response(model)


@router.get(
    "/tasks", response_model=list[TaskResponse], summary="Lister les Cognitive Tasks"
)
def list_tasks(db: Session = Depends(get_db)) -> list[TaskResponse]:
    """Lister toutes les Cognitive Tasks enregistrées."""
    models = db.query(TaskModel).order_by(TaskModel.created_at.desc()).all()
    return [_model_to_response(m) for m in models]


@router.get(
    "/tasks/{id}", response_model=TaskResponse, summary="Récupérer une Cognitive Task"
)
def get_task(id: str, db: Session = Depends(get_db)) -> TaskResponse:
    """Récupérer le détail d'une tâche cognitive par son identifiant."""
    return _model_to_response(_get_task_or_404(db, id))


@router.put(
    "/tasks/{id}",
    response_model=TaskResponse,
    summary="Mettre à jour une Cognitive Task",
)
def update_task(
    id: str,
    payload: TaskUpdate,
    db: Session = Depends(get_db),
) -> TaskResponse:
    """Mettre à jour les champs d'une tâche cognitive."""
    model = _get_task_or_404(db, id)

    if payload.name is not None:
        model.name = payload.name
    if payload.objective is not None:
        model.objective = payload.objective
    if payload.pipeline is not None:
        _check_pipeline(payload.pipeline)
        model.pipeline = payload.pipeline
    if payload.task_constraints is not None:
        model.task_constraints_json = payload.task_constraints.model_dump_json()
    if payload.tools_allowed is not None:
        model.tools_allowed_json = json.dumps(payload.tools_allowed)
    if payload.subtasks is not None:
        model.subtasks_json = json.dumps([st.model_dump() for st in payload.subtasks])
    if payload.evaluation_criteria is not None:
        model.evaluation_criteria_json = json.dumps(
            [c.model_dump() for c in payload.evaluation_criteria]
        )

    model.updated_at = datetime.now(timezone.utc)
    _check_declaration(db, model)
    _commit(db)
    db.refresh(model)
    return _model_to_response(model)


@router.delete(
    "/tasks/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer une Cognitive Task",
)
def delete_task(id: str, db: Session = Depends(get_db)) -> None:
    """Supprimer une tâche cognitive de la base de données."""
    db.delete(_get_task_or_404(db, id))
    _commit(db)


@router.post(
    "/tasks/{id}/execute",
    response_model=ExecutionDetailResponse,
    summary="Exécuter une Cognitive Task",
)
def execute_task(
    id: str,
    payload: ExecuteTaskRequest | None = None,
    db: Session = Depends(get_db),
) -> ExecutionDetailResponse:
    """Exécuter une tâche cognitive : compilation, exécution, traces, métriques.

    Une tâche avec nœud Human-in-the-Loop s'arrête avec le statut
    ``awaiting_approval`` ; ``POST /executions/{id}/resume`` la poursuit.
    """
    task = _get_task_or_404(db, id)
    try:
        record = start_execution(db, task, payload or ExecuteTaskRequest())
    except ExecutionRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return to_detail(record)
