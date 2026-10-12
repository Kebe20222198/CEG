"""Executor and judge backed by real language models.

``LLMExecutor`` runs each step on the model the Runtime Decision Engine chose:
it sends the step's objective and its inputs (graph inputs and upstream
outputs) and asks for a JSON object. The keys that the plan needs from a step
--- the condition read by a ``run_if``, the flag read by a ``repeat`` --- are
required by name, so a real model can drive the plan's control flow.

A call that fails (network, HTTP error, answer that is not the required JSON)
raises ``ExecutionError`` with what it cost: the engine's fallbacks (retry,
escalation) then apply, as with simulated models.

``LLMJudgeClient`` implements the ``JudgeClient`` protocol with a real model,
so quality can be measured on real outputs (LLM-as-a-judge).
"""

from __future__ import annotations

import json
from typing import Any

from ceg.compiler.mock_executor import ExecutionError, ExecutionResult
from ceg.evaluation.models import Criterion, CriterionScore, JudgeVerdict
from ceg.llm.client import ChatClient, LLMError
from ceg.llm.providers import providers
from ceg.models.graph import CEGGraph, EdgeType
from ceg.runtime.decision_engine import ModelProfile

MAX_INPUT_CHARS = 8000

SYSTEM_PROMPT = (
    "Tu exécutes une étape d'un workflow. Réponds uniquement par un objet JSON "
    "valide, sans texte autour ni bloc de code."
)


class LLMExecutor:
    """Run workflow steps on real models (Gemini, Groq, Ollama).

    Args:
        graph: The planned graph: gives the task objective and the keys each
            step must return.
        clients: A client per provider (default: from ``providers()``).
        confidence: Confidence reported for a successful step. The engine
            uses it as the quality observed by the learned optimiser; a judge
            can measure quality separately.
    """

    def __init__(
        self,
        graph: CEGGraph,
        *,
        clients: dict[str, ChatClient] | None = None,
        confidence: float = 0.8,
    ) -> None:
        self.objective = graph.task.objective if graph.task else ""
        self.required_keys = _required_keys(graph)
        self._clients = dict(clients or {})
        self.confidence = confidence
        self.calls: list[dict[str, Any]] = []

    def execute(
        self,
        node_id: str,
        objective: str,
        inputs: dict[str, Any],
        attempt: int = 1,
        model: ModelProfile | None = None,
    ) -> ExecutionResult:
        """Send the step to ``model`` and return its JSON answer."""
        if model is None or model.provider is None or model.provider_model is None:
            raise ExecutionError(node_id, "no real model selected for this step")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._prompt(node_id, objective, inputs)},
        ]
        try:
            response = self._client(model.provider).chat(model.provider_model, messages)
        except LLMError as exc:
            self._record(node_id, model, attempt, ok=False, error=str(exc))
            raise ExecutionError(node_id, f"{model.name}: {exc}") from exc

        cost = model.cost_of(response.input_tokens, response.output_tokens)
        try:
            output = parse_json_object(response.text)
            missing = [
                k for k in self.required_keys.get(node_id, []) if k not in output
            ]
            if missing:
                raise ValueError(f"clés manquantes : {missing}")
        except ValueError as exc:
            self._record(node_id, model, attempt, ok=False, error=str(exc),
                         response=response)  # fmt: skip
            raise ExecutionError(
                node_id,
                f"{model.name}: réponse inutilisable ({exc})",
                cost=cost,
                latency_ms=response.latency_ms,
            ) from exc

        self._record(node_id, model, attempt, ok=True, response=response)
        return ExecutionResult(
            output=output,
            cost=cost,
            latency_ms=response.latency_ms,
            confidence=self.confidence,
        )

    def _prompt(self, node_id: str, objective: str, inputs: dict[str, Any]) -> str:
        data = json.dumps(inputs, ensure_ascii=False, default=str)
        if len(data) > MAX_INPUT_CHARS:
            data = data[:MAX_INPUT_CHARS] + " …(tronqué)"
        lines = [
            f"Objectif du workflow : {self.objective}",
            f"Étape : {node_id}",
            f"Objectif de l'étape : {objective}",
            "",
            "Données disponibles (entrées du workflow et résultats des étapes "
            "précédentes), en JSON :",
            data,
            "",
        ]
        lines.append(
            "Réponds par un objet JSON qui contient le résultat complet de l'étape."
        )
        for key in self.required_keys.get(node_id, []):
            lines.append(
                f"En plus de ce résultat, l'objet doit contenir la clé booléenne "
                f"« {key} » (true ou false)."
            )
        return "\n".join(lines)

    def _client(self, provider: str) -> ChatClient:
        if provider not in self._clients:
            known = providers()
            if provider not in known:
                raise LLMError(f"unknown provider '{provider}'")
            self._clients[provider] = known[provider].client()
        return self._clients[provider]

    def _record(
        self,
        node_id: str,
        model: ModelProfile,
        attempt: int,
        *,
        ok: bool,
        error: str | None = None,
        response: Any = None,
    ) -> None:
        self.calls.append(
            {
                "node_id": node_id,
                "model": model.name,
                "attempt": attempt,
                "ok": ok,
                "error": error,
                "input_tokens": getattr(response, "input_tokens", 0),
                "output_tokens": getattr(response, "output_tokens", 0),
                "latency_ms": getattr(response, "latency_ms", 0.0),
            }
        )


class LLMJudgeClient:
    """LLM-as-a-judge: a real model scores a step's output on criteria."""

    def __init__(self, client: ChatClient, model: str) -> None:
        self.client = client
        self.model = model

    def evaluate(
        self,
        node_id: str,
        node_output: dict[str, Any],
        criteria: list[Criterion],
        node_objective: str = "",
    ) -> JudgeVerdict:
        """Score ``node_output`` on each criterion, between 0 and 1.

        Raises:
            LLMError: If the judge cannot be reached or answers unusably.
        """
        output = json.dumps(node_output, ensure_ascii=False, default=str)
        criteria_text = "\n".join(f"- « {c.name} » : {c.description}" for c in criteria)
        prompt = (
            f"Objectif de l'étape : {node_objective}\n\n"
            f"Sortie produite (JSON) :\n{output[:MAX_INPUT_CHARS]}\n\n"
            f"Évalue cette sortie sur chaque critère :\n{criteria_text}\n\n"
            "Réponds par un objet JSON de la forme "
            '{"<critère>": {"score": <nombre entre 0 et 1>, '
            '"justification": "<une phrase>"}}.'
        )
        response = self.client.chat(
            self.model,
            [
                {
                    "role": "system",
                    "content": "Tu es un évaluateur exigeant et impartial.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        try:
            scores = parse_json_object(response.text)
        except ValueError as exc:
            raise LLMError(f"judge answer unusable: {exc}") from exc
        result = []
        for criterion in criteria:
            entry = scores.get(criterion.name, {})
            value = entry.get("score", 0.0) if isinstance(entry, dict) else entry
            try:
                score = min(1.0, max(0.0, float(value)))
            except (TypeError, ValueError):
                score = 0.0
            justification = (
                entry.get("justification", "") if isinstance(entry, dict) else ""
            )
            result.append(
                CriterionScore(
                    criterion_name=criterion.name,
                    score=score,
                    justification=str(justification),
                    passed=score >= 0.5,
                )
            )
        return JudgeVerdict(
            node_id=node_id, criteria_scores=result, raw_judge_response=response.text
        )


def parse_json_object(text: str) -> dict[str, Any]:
    """The JSON object in a model's answer (tolerates a code fence or prose).

    Raises:
        ValueError: If no JSON object can be read.
    """
    text = text.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("pas d'objet JSON") from None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON invalide : {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError("la réponse n'est pas un objet JSON")
    return value


def _required_keys(graph: CEGGraph) -> dict[str, list[str]]:
    """Keys each step must return: conditions and loop flags read on it."""
    keys: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.edge_type == EdgeType.CONDITIONAL:
            keys.setdefault(edge.source, []).append(edge.condition or "condition")
        elif edge.edge_type == EdgeType.LOOP:
            keys.setdefault(edge.source, []).append(edge.condition or "should_loop")
    for node in graph.nodes:
        if node.subgraph is not None:
            for node_id, sub_keys in _required_keys(node.subgraph).items():
                keys.setdefault(node_id, []).extend(sub_keys)
    return keys
