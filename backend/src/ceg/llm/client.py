"""A minimal client for OpenAI-compatible chat APIs.

Gemini, Groq and Ollama all expose the OpenAI ``/chat/completions`` and
``/models`` endpoints, so one client serves the three providers. It uses the
standard library only (no SDK to install) and takes an injectable transport,
so tests never touch the network.

API keys are read from the environment by ``ceg.llm.providers`` and only ever
sent in the ``Authorization`` header: never logged, never stored in traces.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# (method, url, headers, body or None, timeout) -> (status, parsed JSON body)
Transport = Callable[
    [str, str, dict[str, str], dict[str, Any] | None, float],
    tuple[int, Any],
]


class LLMError(Exception):
    """A provider call failed (network, HTTP error, unusable answer)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ChatResponse:
    """The answer of one chat call."""

    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


def http_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: float,
) -> tuple[int, Any]:
    """Default transport: one HTTP request with urllib."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"null")
        except json.JSONDecodeError:
            payload = None
        return exc.code, payload
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"{url} unreachable: {exc}") from exc


class ChatClient:
    """Chat completions on one OpenAI-compatible endpoint.

    Args:
        base_url: e.g. ``https://api.groq.com/openai/v1``.
        api_key: Sent as a bearer token (None for a local Ollama).
        transport: How requests are sent (tests inject a fake one).
        timeout: Seconds before a call is abandoned.
        max_rate_retries: Retries after an HTTP 429 (rate limit), waiting as
            the provider asks (``retry_after``) or with exponential backoff.
        sleep: Waiting function (tests replace it).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        transport: Transport = http_transport,
        timeout: float = 120.0,
        max_rate_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport
        self.timeout = timeout
        self.max_rate_retries = max_rate_retries
        self._sleep = sleep

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 800,
        json_mode: bool = True,
    ) -> ChatResponse:
        """One chat completion; ``json_mode`` asks for a JSON object answer.

        Raises:
            LLMError: On a network or HTTP error, or an empty answer.
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        start = time.perf_counter()
        status, payload = self._request("POST", "/chat/completions", body)
        if status == 400 and json_mode and "response_format" in json.dumps(payload):
            # A provider without JSON mode: ask again, the prompt still asks
            # for JSON and the answer is parsed leniently.
            body.pop("response_format")
            status, payload = self._request("POST", "/chat/completions", body)
        latency_ms = (time.perf_counter() - start) * 1000
        if status != 200:
            raise LLMError(f"HTTP {status}: {_error_text(payload)}", status=status)
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected answer: {str(payload)[:200]}") from exc
        if not text.strip():
            raise LLMError("empty answer")
        usage = payload.get("usage") or {}
        return ChatResponse(
            text=text,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency_ms,
        )

    def list_models(self) -> list[str]:
        """Identifiers of the models this endpoint serves."""
        status, payload = self._request("GET", "/models", None)
        if status != 200:
            raise LLMError(f"HTTP {status}: {_error_text(payload)}", status=status)
        return sorted(
            str(m.get("id", "")).removeprefix("models/")
            for m in (payload or {}).get("data", [])
        )

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None
    ) -> tuple[int, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        delay = 2.0
        for attempt in range(self.max_rate_retries + 1):
            status, payload = self._transport(
                method, self.base_url + path, headers, body, self.timeout
            )
            if status != 429 or attempt == self.max_rate_retries:
                return status, payload
            self._sleep(_retry_after(payload) or delay)
            delay *= 2
        return status, payload  # pragma: no cover - loop always returns


def _error_text(payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error", payload)
        if isinstance(error, dict):
            return str(error.get("message", error))[:300]
        return str(error)[:300]
    if isinstance(payload, list) and payload:
        return _error_text(payload[0])
    return str(payload)[:300]


def _retry_after(payload: Any) -> float | None:
    """Seconds to wait, when a rate-limit answer says so."""
    text = json.dumps(payload) if payload is not None else ""
    for marker in ("try again in ", "retry in ", 'retryDelay": "'):
        index = text.find(marker)
        if index >= 0:
            number = ""
            for char in text[index + len(marker) :]:
                if char.isdigit() or char == ".":
                    number += char
                else:
                    break
            if number:
                return min(float(number) + 0.5, 60.0)
    return None
