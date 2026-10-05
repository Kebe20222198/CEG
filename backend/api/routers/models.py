"""Available models endpoint router."""

from fastapi import APIRouter
from api.schemas import ModelInfo

router = APIRouter(tags=["Models"])

AVAILABLE_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="gpt-4o-mini",
        name="GPT-4o Mini (Mock Fast)",
        tier="fast",
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.00060,
    ),
    ModelInfo(
        id="gemini-1.5-flash",
        name="Gemini 1.5 Flash (Mock Fast)",
        tier="fast",
        cost_per_1k_input=0.000075,
        cost_per_1k_output=0.00030,
    ),
    ModelInfo(
        id="gpt-4o",
        name="GPT-4o (Mock Balanced)",
        tier="balanced",
        cost_per_1k_input=0.0025,
        cost_per_1k_output=0.0100,
    ),
    ModelInfo(
        id="gemini-1.5-pro",
        name="Gemini 1.5 Pro (Mock Balanced)",
        tier="balanced",
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.0050,
    ),
    ModelInfo(
        id="claude-3-5-sonnet",
        name="Claude 3.5 Sonnet (Mock Quality)",
        tier="quality",
        cost_per_1k_input=0.0030,
        cost_per_1k_output=0.0150,
    ),
]


@router.get("/models", response_model=list[ModelInfo], summary="Modèles disponibles")
def list_models() -> list[ModelInfo]:
    """Retourne la liste des modèles LLM disponibles et leurs tiers tarifaires."""
    return AVAILABLE_MODELS
