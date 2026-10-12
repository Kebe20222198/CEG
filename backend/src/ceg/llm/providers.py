"""Real model providers and the registry of real models.

Three providers, all reached through their OpenAI-compatible API:

  - ``gemini``: Google Gemini (key ``GEMINI_API_KEY``);
  - ``groq``: Groq (key ``GROQ_API_KEY``);
  - ``ollama``: a local Ollama server, e.g. in Docker (``OLLAMA_BASE_URL``,
    default ``http://localhost:11434/v1``; no key).

Keys come from the environment only (or a ``.env`` file loaded by the CLI,
which Git ignores). ``REAL_MODELS`` lists the default models; model ids and
prices change over time: ``ceg llm check`` compares them with what each
provider actually serves, and ``load_models()`` replaces them from a JSON
file without touching the code.

Prices are public list prices per million tokens (indicative, checked in
October 2026); they give every call a comparable cost even on a free tier.
A local model costs 0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ceg.llm.client import ChatClient, LLMError
from ceg.models.node import ModelTierHint
from ceg.runtime.decision_engine import _ALL_CAPABILITIES, ModelProfile

# Typical size of one workflow step, to estimate a call's cost before it runs.
EXPECTED_INPUT_TOKENS = 1000
EXPECTED_OUTPUT_TOKENS = 300


@dataclass(frozen=True)
class Provider:
    """An OpenAI-compatible endpoint."""

    name: str
    base_url: str
    key_env: str | None

    def api_key(self) -> str | None:
        return os.environ.get(self.key_env) if self.key_env else None

    def is_configured(self) -> bool:
        """A key is set; a local provider (no key) must answer instead."""
        if self.key_env is not None:
            return bool(self.api_key())
        return _answers(self.base_url)

    def client(self, timeout: float = 120.0) -> ChatClient:
        return ChatClient(self.base_url, self.api_key(), timeout=timeout)


def providers() -> dict[str, Provider]:
    """The known providers (base URLs read from the environment)."""
    return {
        "gemini": Provider(
            "gemini",
            os.environ.get(
                "GEMINI_BASE_URL",
                "https://generativelanguage.googleapis.com/v1beta/openai",
            ),
            "GEMINI_API_KEY",
        ),
        "groq": Provider(
            "groq",
            os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            "GROQ_API_KEY",
        ),
        "ollama": Provider(
            "ollama",
            os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            None,
        ),
    }


def real_model(
    provider: str,
    provider_model: str,
    tier: ModelTierHint,
    *,
    input_price: float,
    output_price: float,
    latency_ms: float,
    prior_quality: float,
) -> ModelProfile:
    """A real model profile; its estimated cost is that of a typical step."""
    profile = ModelProfile(
        name=f"{provider}:{provider_model}",
        tier=tier,
        estimated_cost=0.0,
        estimated_latency_ms=latency_ms,
        supported_capabilities=list(_ALL_CAPABILITIES),
        provider=provider,
        provider_model=provider_model,
        input_price_per_mtok=input_price,
        output_price_per_mtok=output_price,
        prior_quality=prior_quality,
    )
    return profile.model_copy(
        update={
            "estimated_cost": profile.cost_of(
                EXPECTED_INPUT_TOKENS, EXPECTED_OUTPUT_TOKENS
            )
        }
    )


FAST, BALANCED, QUALITY = (
    ModelTierHint.FAST,
    ModelTierHint.BALANCED,
    ModelTierHint.QUALITY,
)

REAL_MODELS: list[ModelProfile] = [
    real_model("groq", "openai/gpt-oss-20b", FAST, input_price=0.10,
               output_price=0.50, latency_ms=1000, prior_quality=0.75),
    real_model("groq", "openai/gpt-oss-120b", BALANCED, input_price=0.15,
               output_price=0.75, latency_ms=1500, prior_quality=0.82),
    real_model("gemini", "gemini-2.5-flash-lite", FAST, input_price=0.10,
               output_price=0.40, latency_ms=1500, prior_quality=0.75),
    real_model("gemini", "gemini-2.5-flash", QUALITY, input_price=0.30,
               output_price=2.50, latency_ms=4000, prior_quality=0.85),
    real_model("ollama", "qwen2.5:3b", FAST, input_price=0.0,
               output_price=0.0, latency_ms=20000, prior_quality=0.60),
]  # fmt: skip


def load_models(path: str | Path) -> list[ModelProfile]:
    """Real models from a JSON file: a list of ``real_model`` arguments.

    Example entry::

        {"provider": "groq", "provider_model": "llama-3.1-8b-instant",
         "tier": "fast", "input_price": 0.05, "output_price": 0.08,
         "latency_ms": 800, "prior_quality": 0.7}
    """
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        real_model(
            e["provider"],
            e["provider_model"],
            ModelTierHint(e["tier"]),
            input_price=float(e.get("input_price", 0.0)),
            output_price=float(e.get("output_price", 0.0)),
            latency_ms=float(e.get("latency_ms", 2000)),
            prior_quality=float(e.get("prior_quality", 0.7)),
        )
        for e in entries
    ]


def available_models(
    models: list[ModelProfile] | None = None,
    only: list[str] | None = None,
) -> list[ModelProfile]:
    """The real models whose provider is configured (and among ``only``)."""
    known = providers()
    return [
        m
        for m in (models if models is not None else REAL_MODELS)
        if m.provider in known
        and known[m.provider].is_configured()
        and (only is None or m.provider in only)
    ]


@lru_cache(maxsize=8)
def _answers(base_url: str) -> bool:
    """A local server answers within two seconds (checked once per URL)."""
    try:
        ChatClient(base_url, timeout=2.0, max_rate_retries=0).list_models()
    except LLMError:
        return False
    return True


def check_provider(provider: Provider) -> tuple[bool, list[str], str]:
    """(reachable, models served, message) for one provider."""
    if provider.key_env is not None and not provider.api_key():
        return False, [], f"clé absente ({provider.key_env})"
    try:
        served = provider.client(timeout=15.0).list_models()
    except LLMError as exc:
        return False, [], str(exc)
    return True, served, f"{len(served)} modèles servis"


def load_dotenv(path: str | Path) -> list[str]:
    """Set ``KEY=value`` lines of a .env file that the environment lacks.

    Returns the names that were set (never the values).
    """
    file = Path(path)
    if not file.is_file():
        return []
    loaded = []
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
