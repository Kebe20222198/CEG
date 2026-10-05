"""Benchmark endpoint router (Stub S6)."""

from datetime import datetime, timezone
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.db import BenchmarkModel, get_db
from api.schemas import BenchmarkRequest, BenchmarkResponse

router = APIRouter(tags=["Benchmark"])


@router.post(
    "/benchmark",
    response_model=BenchmarkResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Lancer un benchmark",
)
def launch_benchmark(
    body: BenchmarkRequest = BenchmarkRequest(),
    db: Session = Depends(get_db),
) -> BenchmarkResponse:
    """Stub minimal qui initialise un benchmark et retourne un 202 Accepted."""
    benchmark_id = f"bench_{uuid.uuid4().hex[:8]}"
    record = BenchmarkModel(
        id=benchmark_id,
        status="ACCEPTED",
        created_at=datetime.now(timezone.utc),
        scenarios_json=json.dumps(body.scenarios),
        results_json=json.dumps({
            "message": "Benchmark stub S6 executed.",
            "task_id": body.task_id,
            "n_runs": body.n_runs,
            "scenarios": body.scenarios,
        }),
    )
    db.add(record)
    db.commit()

    return BenchmarkResponse(
        id=benchmark_id,
        status="ACCEPTED",
        message=f"Benchmark {benchmark_id} planifié avec succès ({body.n_runs} runs par scénario).",
        results=json.loads(record.results_json),
    )


@router.get(
    "/benchmark/{id}/results",
    response_model=BenchmarkResponse,
    summary="Résultats du benchmark",
)
def get_benchmark_results(
    id: str,
    db: Session = Depends(get_db),
) -> BenchmarkResponse:
    """Récupère l'état et les résultats d'un benchmark."""
    record = db.query(BenchmarkModel).filter(BenchmarkModel.id == id).first()
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Benchmark '{id}' non trouvé.",
        )

    results = json.loads(record.results_json) if record.results_json else None

    return BenchmarkResponse(
        id=record.id,
        status=record.status,
        message=f"Benchmark {record.id} status: {record.status}",
        results=results,
    )
