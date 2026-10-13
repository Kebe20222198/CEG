"""Models and pipelines endpoints router."""

from fastapi import APIRouter

from api.pipelines import pipelines
from api.schemas import (
    BackendInfo,
    LLMProviderInfo,
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
    """Modèles parmi lesquels le Runtime Decision Engine choisit : simulés, puis
    réels (Gemini, Groq, Ollama), avec leur disponibilité."""
    from ceg.llm.providers import REAL_MODELS, available_models

    usable = {m.name for m in available_models(REAL_MODELS)}
    return [
        ModelInfo(
            id=m.name,
            tier=m.tier.value,
            estimated_cost=m.estimated_cost,
            estimated_latency_ms=m.estimated_latency_ms,
            supported_capabilities=m.supported_capabilities,
            provider=m.provider,
            available=m.provider is None or m.name in usable,
        )
        for m in [*DEFAULT_MODEL_REGISTRY, *REAL_MODELS]
    ]


@router.get(
    "/llm/providers",
    response_model=list[LLMProviderInfo],
    summary="Fournisseurs de modèles réels",
)
def list_llm_providers() -> list[LLMProviderInfo]:
    """Gemini, Groq, Ollama : clé présente ou serveur local joignable, et
    modèles utilisables (champ ``llm_providers`` d'une exécution)."""
    from ceg.llm.providers import REAL_MODELS, providers

    return [
        LLMProviderInfo(
            id=name,
            configured=provider.is_configured(),
            models=[m.name for m in REAL_MODELS if m.provider == name],
        )
        for name, provider in providers().items()
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
        for name, spec in pipelines().items()
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
