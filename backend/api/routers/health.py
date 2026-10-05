"""Health check endpoint router."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.db import get_db
from api.schemas import HealthResponse
from ceg import __version__

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
        version=__version__,
        sqlite=sqlite_status,
    )
