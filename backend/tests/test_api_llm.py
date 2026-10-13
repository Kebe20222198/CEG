"""Real models through the API (and so the Studio), without any network call."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import ceg.llm.providers as providers_module
from api.main import app
from ceg.llm.client import ChatClient

REPO_TICKETS = (
    Path(__file__).resolve().parents[2] / "workflows" / "tri_tickets_support.py"
)
PROVIDER_KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    shutil.copy(REPO_TICKETS, Path(os.environ["CEG_WORKFLOWS_DIR"]))
    with TestClient(app) as c:
        # No real provider: no key, no local server. Done once the app has
        # started: CrewAI, imported at startup, loads the repository's .env.
        for key in PROVIDER_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(providers_module, "_answers", lambda url: False)
        c.post("/workflows/refresh")
        yield c


def _fake_groq(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Groq "configured", answering every step with a fake model."""
    monkeypatch.setenv("GROQ_API_KEY", "test")
    requests: list[dict[str, Any]] = []

    def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout: float,
    ) -> tuple[int, Any]:
        assert body is not None
        requests.append(body)
        prompt = body["messages"][-1]["content"]
        answer: dict[str, Any] = {"resultat": "ok"}
        if "Étape : detecter_urgences" in prompt:
            answer = {"urgents": ["T-102"], "a_des_urgences": True}
        content = json.dumps(answer)
        usage = {"prompt_tokens": 500, "completion_tokens": 100}
        return 200, {"choices": [{"message": {"content": content}}], "usage": usage}

    monkeypatch.setattr(
        providers_module.Provider,
        "client",
        lambda self, timeout=120.0: ChatClient(
            self.base_url, "test", transport=transport
        ),
    )
    return requests


class TestRealModelsThroughTheApi:
    def test_providers_and_models_are_listed(self, client: TestClient) -> None:
        providers = {p["id"]: p for p in client.get("/llm/providers").json()}
        assert set(providers) == {"gemini", "groq", "ollama"}
        assert not any(p["configured"] for p in providers.values())
        models = client.get("/models").json()
        real = [m for m in models if m["provider"]]
        assert real and not any(m["available"] for m in real)
        assert all(m["available"] for m in models if m["provider"] is None)

    def test_a_provider_without_key_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/tasks/tri_tickets_support/execute", json={"llm_providers": ["groq"]}
        )
        assert response.status_code == 400
        assert "No real model available" in response.json()["detail"]

    def test_an_unknown_provider_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/tasks/tri_tickets_support/execute", json={"llm_providers": ["openai"]}
        )
        assert response.status_code == 400
        assert "Unknown model providers" in response.json()["detail"]

    def test_a_csv_workflow_sends_the_rows_not_the_path(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requests = _fake_groq(monkeypatch)
        response = client.post(
            "/tasks/analyse_ventes_alertes/execute", json={"llm_providers": ["groq"]}
        )
        assert response.status_code == 200, response.text
        prompt = requests[0]["messages"][-1]["content"]
        assert "csv_path" not in prompt and ".csv" in prompt  # the file's name
        assert (
            '"colonnes": ["date", "region", "montant", "produit", "quantite"]' in prompt
        )
        assert "Nord" in prompt  # the rows themselves

    def test_a_run_on_real_models(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requests = _fake_groq(monkeypatch)
        response = client.post(
            "/tasks/tri_tickets_support/execute", json={"llm_providers": ["all"]}
        )
        assert response.status_code == 200, response.text
        run = response.json()
        assert run["status"] == "completed"
        assert run["models"] == "gemini,groq,ollama"
        log = run["workflow_state"]["execution_log"]
        used = {e["model_used"] for e in log if e.get("model_used")}
        assert used and all(name.startswith("groq:") for name in used)
        assert len(requests) == 5  # one call per step, escalation included
        listed = {e["id"]: e for e in client.get("/executions").json()}
        assert listed[run["id"]]["models"] == "gemini,groq,ollama"
        grid = client.get("/workflows/tri_tickets_support/grid").json()
        assert grid["runs"][-1]["models"] == "gemini,groq,ollama"
