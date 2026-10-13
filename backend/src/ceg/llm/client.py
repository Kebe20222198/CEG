"""A minimal client for OpenAI-compatible chat APIs.

Gemini, Groq and Ollama all expose the OpenAI ``/chat/completions`` and
``/models`` endpoints, so one client serves the three providers. It uses the
standard library only (no SDK to install) and takes an injectable transport,
so tests never touch the network.

API keys are read from the environment by ``ceg.llm.providers`` and only ever
sent in the ``Authorization`` header: never logged, never stored in traces.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

USER_AGENT = "ceg/0.6 (+https://github.com/Kebe20222198/CEG)"

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


# Seconds to open a TCP connection to one address before trying the next one.
# Some networks silently drop a provider's addresses: without this, a call
# would wait for the whole timeout on a dead address.
CONNECT_TIMEOUT = 3.0


def _connect(host: str, port: int) -> socket.socket:
    """A TCP connection to the first address of ``host`` that answers."""
    last_error: OSError | None = None
    for family, kind, proto, _, address in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    ):
        sock = socket.socket(family, kind, proto)
        sock.settimeout(CONNECT_TIMEOUT)
        try:
            sock.connect(address)
        except OSError as exc:
            sock.close()
            last_error = exc
            continue
        return sock
    raise last_error or OSError(f"no address for {host}")


class _HTTPConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = _connect(self.host, self.port)
        self.sock.settimeout(self.timeout)


class _HTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        sock = _connect(self.host, self.port)
        sock.settimeout(self.timeout)
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(sock, server_hostname=self.host)


def http_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: float,
) -> tuple[int, Any]:
    """Default transport: one HTTP request, failing over dead addresses fast."""
    parts = urllib.parse.urlsplit(url)
    secure = parts.scheme == "https"
    connection_class = _HTTPSConnection if secure else _HTTPConnection
    port = parts.port or (443 if secure else 80)
    connection = connection_class(parts.hostname or "", port, timeout=timeout)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    data = json.dumps(body).encode() if body is not None else None
    try:
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        raw = response.read()
    except (OSError, http.client.HTTPException) as exc:
        raise LLMError(f"{url} unreachable: {exc}") from exc
    finally:
        connection.close()
    try:
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        payload = raw.decode(errors="replace")[:300]
    return response.status, payload


class ChatClient:
    """Chat completions on one OpenAI-compatible endpoint.

    Args:
        base_url: e.g. ``https://api.groq.com/openai/v1``.
        api_key: Sent as a bearer token (None for a local Ollama).
        transport: How requests are sent (tests inject a fake one).
        timeout: Seconds before a call is abandoned.
        max_rate_retries: Retries after an HTTP 429 (rate limit) or 503
            (overloaded), waiting as the provider asks (``retry_after``) or
            with exponential backoff.
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
        max_tokens: int = 2000,
        json_mode: bool = True,
        timeout: float | None = None,
    ) -> ChatResponse:
        """One chat completion; ``json_mode`` asks for a JSON object answer.

        ``timeout`` (seconds) bounds the whole call, rate-limit waits
        included; default: the client's timeout.

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
        deadline = start + (timeout if timeout is not None else self.timeout)
        status, payload = self._request("POST", "/chat/completions", body, deadline)
        if status == 400 and json_mode and "response_format" in json.dumps(payload):
            # A provider without JSON mode: ask again, the prompt still asks
            # for JSON and the answer is parsed leniently.
            body.pop("response_format")
            status, payload = self._request("POST", "/chat/completions", body, deadline)
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
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        deadline: float | None = None,
    ) -> tuple[int, Any]:
        # An explicit User-Agent: Groq's firewall rejects urllib's default one.
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        delay = 2.0
        for attempt in range(self.max_rate_retries + 1):
            timeout = self.timeout
            if deadline is not None:
                timeout = deadline - time.perf_counter()
                if timeout <= 0:
                    raise LLMError(f"{self.base_url + path}: timed out")
            status, payload = self._transport(
                method, self.base_url + path, headers, body, timeout
            )
            # 429: rate limit; 503: model overloaded. Both are transient.
            if status not in (429, 503) or attempt == self.max_rate_retries:
                return status, payload
            wait = _retry_after(payload) or delay
            if deadline is not None and time.perf_counter() + wait >= deadline:
                return status, payload  # no time left to wait and retry
            self._sleep(wait)
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
