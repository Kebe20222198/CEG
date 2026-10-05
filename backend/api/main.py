"""CEG (Cognitive Execution Graph) FastAPI Main Application (Semaine 6)."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db import init_db
from api.routers import benchmark, executions, health, models, tasks
from api.seed import seed_database


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for database initialization and seeding on startup."""
    init_db()
    seed_database()
    yield


app = FastAPI(
    title="Cognitive Execution Graph (CEG) API",
    description=(
        "API REST pour l'orchestration, la persistance et l'évaluation de graphes "
        "d'exécution cognitive (CEG). Semaine 6 du projet CEG."
    ),
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Allow CORS for React dashboard frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
def root_redirect():
    """Root redirect to OpenAPI documentation."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/docs")
