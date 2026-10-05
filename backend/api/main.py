"""CEG (Cognitive Execution Graph) FastAPI Main Application (Semaine 6)."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from api.db import init_db
from api.routers import benchmark, executions, health, models, tasks
from api.seed import seed_database
from ceg import __version__

# Comma-separated list of origins allowed to call the API from a browser.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CEG_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create the tables on startup, and seed demo data unless CEG_SEED=0."""
    init_db()
    if os.getenv("CEG_SEED", "1") != "0":
        seed_database()
    yield


app = FastAPI(
    title="Cognitive Execution Graph (CEG) API",
    description=(
        "API REST pour l'orchestration, la persistance et l'évaluation de graphes "
        "d'exécution cognitive (CEG). Semaine 6 du projet CEG."
    ),
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Allow the React dashboard (CEG_CORS_ORIGINS) to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers matching Table 7.5 exact endpoint paths
app.include_router(health.router)
app.include_router(tasks.router)
app.include_router(executions.router)
app.include_router(benchmark.router)
app.include_router(models.router)


@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    """Root redirect to OpenAPI documentation."""
    return RedirectResponse(url="/docs")
