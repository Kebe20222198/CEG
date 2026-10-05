"""Models and pipelines endpoints router."""

from fastapi import APIRouter

from api.pipelines import PIPELINES
from api.schemas import ModelInfo, PipelineInfo
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
