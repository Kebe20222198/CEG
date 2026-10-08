"""Models and pipelines endpoints router."""

from fastapi import APIRouter

from api.pipelines import PIPELINES
from api.schemas import (
    BackendInfo,
    ModelInfo,
    OptimizerStatisticsResponse,
    PipelineInfo,
)
from api.services import LEARNED_STATISTICS
from ceg.backends import available_backends
from ceg.runtime.decision_engine import DEFAULT_MODEL_REGISTRY

router = APIRouter(tags=["Models"])


@router.get("/models", response_model=list[ModelInfo], summary="Modèles disponibles")
def list_models() -> list[ModelInfo]:
    """Modèles (simulés) parmi lesquels le Runtime Decision Engine choisit."""
    return [
        ModelInfo(
            id=m.name,
            tier=m.tier.value,
            estimated_cost=m.estimated_cost,
            estimated_latency_ms=m.estimated_latency_ms,
            supported_capabilities=m.supported_capabilities,
        )
        for m in DEFAULT_MODEL_REGISTRY
    ]


@router.get(
    "/backends", response_model=list[BackendInfo], summary="Backends d'exécution"
)
def list_backends() -> list[BackendInfo]:
    """Moteurs capables d'exécuter un plan (champ ``backend`` de l'exécution)."""
    return [
        BackendInfo(id=b.name, supports_hitl=b.supports_hitl)
        for b in available_backends()
    ]


@router.get(
    "/pipelines", response_model=list[PipelineInfo], summary="Pipelines disponibles"
)
def list_pipelines() -> list[PipelineInfo]:
    """Modèles de pipeline utilisables dans le champ ``pipeline`` d'une tâche."""
    return [
        PipelineInfo(
            id=name,
            uses_csv=spec.uses_csv,
            criteria=[c.name for c in spec.criteria],
        )
        for name, spec in PIPELINES.items()
    ]


@router.get(
    "/optimizer/statistics",
    response_model=OptimizerStatisticsResponse,
    summary="Ce que l'optimiseur appris sait des modèles",
)
def optimizer_statistics() -> OptimizerStatisticsResponse:
    """Observations par (modèle, capacité) : appels, taux de succès, qualité."""
    return OptimizerStatisticsResponse(
        prior_weight=LEARNED_STATISTICS.prior_weight,
        exploration=LEARNED_STATISTICS.exploration,
        observations=LEARNED_STATISTICS.summary(),
    )
