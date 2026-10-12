"""Real-model support (ceg.llm), tested without any network call.

A fake transport stands in for the providers' HTTP APIs: these tests run in
CI with no API key and no Ollama server.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import ceg.llm.providers as providers_module
from ceg.backends import get_backend
from ceg.compiler.mock_executor import ExecutionError
from ceg.evaluation.models import Criterion
from ceg.llm.client import ChatClient, LLMError
from ceg.llm.executor import LLMExecutor, LLMJudgeClient, parse_json_object
from ceg.llm.providers import (
    REAL_MODELS,
    available_models,
    load_dotenv,
    load_models,
    real_model,
)
from ceg.models.node import ModelTierHint
from ceg.planner import plan
from ceg.registry import WorkflowRegistry
from ceg.runtime.decision_engine import RuntimeDecisionEngine

REPO_WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"


def _chat_payload(
    content: str, prompt: int = 100, completion: int = 20
) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


class FakeTransport:
    """Answers chat requests with ``reply(body)``; records every request."""

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.requests: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        timeout: float,
    ) -> tuple[int, Any]:
        self.requests.append(
            {"method": method, "url": url, "headers": headers, "body": body}
        )
        status, payload = self.reply(body)
        return int(status), payload


class TestChatClient:
    def test_chat_returns_text_tokens_and_sends_the_key(self) -> None:
        transport = FakeTransport(
            lambda body: (200, _chat_payload('{"ok": true}', 120, 30))
        )
        client = ChatClient("https://api.example/v1/", "secret", transport=transport)
        response = client.chat("m", [{"role": "user", "content": "x"}])
        assert response.text == '{"ok": true}'
        assert (response.input_tokens, response.output_tokens) == (120, 30)
        request = transport.requests[0]
        assert request["url"] == "https://api.example/v1/chat/completions"
        assert request["headers"]["Authorization"] == "Bearer secret"
        assert request["body"]["response_format"] == {"type": "json_object"}

    def test_no_key_no_authorization_header(self) -> None:
        transport = FakeTransport(lambda body: (200, _chat_payload("{}")))
        ChatClient("http://localhost:11434/v1", transport=transport).chat("m", [])
        assert "Authorization" not in transport.requests[0]["headers"]

    def test_rate_limit_is_retried_after_the_advertised_delay(self) -> None:
        answers = iter(
            [
                (429, {"error": {"message": "Rate limit. Please try again in 1.5s."}}),
                (200, _chat_payload("{}")),
            ]
        )
        waits: list[float] = []
        client = ChatClient(
            "https://x/v1",
            transport=FakeTransport(lambda body: next(answers)),
            sleep=waits.append,
        )
        client.chat("m", [])
        assert waits == [2.0]  # 1.5 s advertised, plus a margin

    def test_provider_without_json_mode_is_asked_again(self) -> None:
        def reply(body: dict[str, Any]) -> tuple[int, Any]:
            if "response_format" in body:
                return 400, {"error": {"message": "response_format is not supported"}}
            return 200, _chat_payload('{"a": 1}')

        transport = FakeTransport(reply)
        ChatClient("https://x/v1", transport=transport).chat("m", [])
        assert len(transport.requests) == 2
        assert "response_format" not in transport.requests[1]["body"]

    def test_http_error_raises(self) -> None:
        transport = FakeTransport(lambda body: (401, {"error": {"message": "bad key"}}))
        with pytest.raises(LLMError, match="HTTP 401: bad key"):
            ChatClient("https://x/v1", transport=transport).chat("m", [])

    def test_list_models(self) -> None:
        transport = FakeTransport(
            lambda body: (
                200,
                {"data": [{"id": "models/gemini-2.5-flash"}, {"id": "b"}]},
            )
        )
        assert ChatClient("https://x/v1", transport=transport).list_models() == [
            "b",
            "gemini-2.5-flash",
        ]


class TestParseJson:
    @pytest.mark.parametrize(
        "text",
        ['{"a": 1}', '```json\n{"a": 1}\n```', 'Voici : {"a": 1} Fin.'],
    )
    def test_reads_the_object(self, text: str) -> None:
        assert parse_json_object(text) == {"a": 1}

    @pytest.mark.parametrize("text", ["pas de json", "[1, 2]", '{"a": '])
    def test_refuses(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_json_object(text)


# ── A real workflow on fake real models ───────────────────────────────────────

FAKE_FAST = real_model(
    "fake", "petit", ModelTierHint.FAST, input_price=1.0, output_price=2.0,
    latency_ms=500, prior_quality=0.7,
)  # fmt: skip
FAKE_GOOD = real_model(
    "fake", "grand", ModelTierHint.QUALITY, input_price=5.0, output_price=10.0,
    latency_ms=1500, prior_quality=0.9,
)  # fmt: skip


def _tickets_graph() -> Any:
    registry = WorkflowRegistry(folder=REPO_WORKFLOWS).load()
    return plan(registry.workflows["tri_tickets_support"].declare())


def _step_reply(urgent: bool) -> Any:
    def reply(body: dict[str, Any]) -> tuple[int, Any]:
        prompt = body["messages"][-1]["content"]
        if "Étape : detecter_urgences" in prompt:
            answer = {"urgents": ["T-102"] if urgent else [], "a_des_urgences": urgent}
        else:
            answer = {"resultat": "ok"}
        return 200, _chat_payload(json.dumps(answer), 1000, 200)

    return reply


class TestLLMExecutor:
    def test_condition_keys_are_required_in_the_prompt(self) -> None:
        graph = _tickets_graph()
        transport = FakeTransport(_step_reply(urgent=True))
        executor = LLMExecutor(
            graph, clients={"fake": ChatClient("https://x/v1", transport=transport)}
        )
        assert executor.required_keys == {"detecter_urgences": ["a_des_urgences"]}
        result = executor.execute("detecter_urgences", "Repérer", {}, model=FAKE_FAST)
        assert result.output["a_des_urgences"] is True
        assert (
            "« a_des_urgences »"
            in transport.requests[0]["body"]["messages"][-1]["content"]
        )
        # 1000 tokens in at 1 $/M + 200 out at 2 $/M
        assert result.cost == pytest.approx(0.0014)

    def test_missing_key_is_a_billed_failure(self) -> None:
        graph = _tickets_graph()
        transport = FakeTransport(
            lambda body: (200, _chat_payload('{"x": 1}', 1000, 200))
        )
        executor = LLMExecutor(
            graph, clients={"fake": ChatClient("https://x/v1", transport=transport)}
        )
        with pytest.raises(ExecutionError) as info:
            executor.execute("detecter_urgences", "Repérer", {}, model=FAKE_FAST)
        assert info.value.cost == pytest.approx(0.0014)
        assert "a_des_urgences" in info.value.reason

    @pytest.mark.parametrize("urgent", [True, False])
    def test_the_model_drives_the_plan(self, urgent: bool) -> None:
        graph = _tickets_graph()
        transport = FakeTransport(_step_reply(urgent))
        executor = LLMExecutor(
            graph, clients={"fake": ChatClient("https://x/v1", transport=transport)}
        )
        engine = RuntimeDecisionEngine(
            available_models=[FAKE_FAST, FAKE_GOOD], max_latency_ms=60_000
        )
        workflow = get_backend("python").compile(
            graph, engine=engine, executor=executor
        )
        state = workflow.invoke({"inputs": {"tickets": []}})
        statuses = state["node_statuses"]
        assert statuses["escalader"] == ("completed" if urgent else "skipped")
        assert statuses["rediger_synthese"] == "completed"
        assert state["total_cost"] > 0


class TestJudge:
    def test_scores_each_criterion(self) -> None:
        answer = {"exactitude": {"score": 0.8, "justification": "Correct."}}
        transport = FakeTransport(lambda body: (200, _chat_payload(json.dumps(answer))))
        judge = LLMJudgeClient(ChatClient("https://x/v1", transport=transport), "juge")
        criteria = [
            Criterion(
                name="exactitude",
                description="La sortie est exacte.",
                weight=1.0,
                evaluation_prompt_template="{output}",
            )
        ]
        verdict = judge.evaluate("n", {"r": 1}, criteria, "Objectif")
        assert verdict.aggregate_quality_score == pytest.approx(0.8)
        assert verdict.criteria_scores[0].justification == "Correct."


class TestProviders:
    def test_only_configured_providers_are_offered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "k")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setattr(providers_module, "_answers", lambda url: False)
        names = {m.provider for m in available_models()}
        assert names == {"groq"}
        assert available_models(only=["gemini"]) == []

    def test_estimated_cost_is_that_of_a_typical_step(self) -> None:
        model = next(
            m for m in REAL_MODELS if m.provider_model == "llama-3.1-8b-instant"
        )
        assert model.estimated_cost == pytest.approx((1000 * 0.05 + 300 * 0.08) / 1e6)

    def test_load_models_from_json(self, tmp_path: Path) -> None:
        path = tmp_path / "modeles.json"
        path.write_text(
            json.dumps(
                [{"provider": "groq", "provider_model": "nouveau", "tier": "fast"}]
            )
        )
        [model] = load_models(path)
        assert model.name == "groq:nouveau" and model.tier == ModelTierHint.FAST

    def test_dotenv_sets_missing_names_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CEG_TEST_A", raising=False)
        monkeypatch.setenv("CEG_TEST_B", "garde")
        env = tmp_path / ".env"
        env.write_text(
            '# commentaire\nCEG_TEST_A="nouvelle"\nexport CEG_TEST_B=autre\n'
        )
        assert load_dotenv(env) == ["CEG_TEST_A"]
        import os

        assert os.environ["CEG_TEST_A"] == "nouvelle"
        assert os.environ["CEG_TEST_B"] == "garde"
        monkeypatch.delenv("CEG_TEST_A")


class TestPortabilityExperiment:
    def test_a_run_is_measured_and_judged(self) -> None:
        from ceg.experiments.portabilite import run_once, summarize, task

        declaration, inputs = task()
        graph = plan(declaration)
        transport = FakeTransport(_step_reply(urgent=True))
        client = ChatClient("https://x/v1", transport=transport)
        judge_answer = {"pertinence": {"score": 1.0}, "exactitude": {"score": 0.6}}
        judge = LLMJudgeClient(
            ChatClient(
                "https://j/v1",
                transport=FakeTransport(
                    lambda body: (200, _chat_payload(json.dumps(judge_answer)))
                ),
            ),
            "juge",
        )
        run = run_once(graph, inputs, [FAKE_FAST, FAKE_GOOD], judge, {"fake": client})
        assert run["completed"] and run["escalated"] and run["urgent_flagged"]
        assert run["quality"] == 0.8
        assert run["cost_usd"] > 0
        summary = summarize([run])
        assert summary["completion"] == 1.0
        assert summary["urgent_detected"] == 1.0
