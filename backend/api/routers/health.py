"""Health check endpoint router."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from api.db import get_db
from api.schemas import HealthResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse, summary="État du système")
def get_health(db: Session = Depends(get_db)) -> HealthResponse:
    """Vérifie le bon fonctionnement du système et la connexion à SQLite."""
    sqlite_status = "connected"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        sqlite_status = "disconnected"

    return HealthResponse(
        status="ok" if sqlite_status == "connected" else "degraded",
        version="0.2.0",
        sqlite=sqlite_status,
    )
