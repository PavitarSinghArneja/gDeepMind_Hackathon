"""Talks to Ollama on localhost. Structured JSON output is enforced with Ollama's `format` schema."""
from __future__ import annotations

import base64
import json
from typing import Callable, Protocol

import httpx


class LLMError(Exception):
    """The model call failed in a way a retry or another model might fix."""


class LLMUnavailable(LLMError):
    """Ollama isn't running or the model isn't pulled. Wait; don't burn retries."""


class LLMTimeout(LLMError):
    pass


class LLMBadOutput(LLMError):
    pass


class LLM(Protocol):
    def generate_json(self, model: str, prompt: str, schema: dict, images: list[bytes] | None = None) -> dict: ...
    def embed(self, text: str) -> list[float]: ...
    def models(self) -> list[str]: ...


def installed(names: list[str], model: str) -> bool:
    return model in names or f"{model}:latest" in names


def _error_text(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error", r.text))[:300]
    except ValueError:
        return r.text[:300]


class OllamaLLM:
    def __init__(self, base_url: str, embed_model: str, timeout_s: float, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.embed_model = embed_model
        self.vision = True  # flips to False if the model build rejects images
        self.client = httpx.Client(timeout=timeout_s, transport=transport)

    def _post(self, path: str, payload: dict) -> httpx.Response:
        try:
            return self.client.post(f"{self.base_url}{path}", json=payload)
        except httpx.TimeoutException as e:
            raise LLMTimeout(f"model call timed out: {e}") from e
        except httpx.TransportError as e:
            raise LLMUnavailable(f"Ollama isn't reachable at {self.base_url}: {e}") from e

    @staticmethod
    def _raise_for(r: httpx.Response) -> None:
        if r.status_code == 404:
            raise LLMUnavailable(f"model not found: {_error_text(r)}")
        if r.status_code >= 400:
            raise LLMError(f"Ollama {r.status_code}: {_error_text(r)}")

    def generate_json(self, model: str, prompt: str, schema: dict, images: list[bytes] | None = None) -> dict:
        message: dict = {"role": "user", "content": prompt}
        send_images = bool(images) and self.vision
        if send_images:
            message["images"] = [base64.b64encode(b).decode("ascii") for b in images]
        r = self._post("/api/chat", {
            "model": model,
            "messages": [message],
            "format": schema,
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0},
        })
        if send_images and r.status_code >= 400 and any(w in _error_text(r).lower() for w in ("image", "vision", "multimodal")):
            self.vision = False
            return self.generate_json(model, prompt, schema, None)
        self._raise_for(r)
        content = r.json().get("message", {}).get("content", "")
        try:
            obj = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMBadOutput(f"not JSON: {content[:200]}") from e
        if not isinstance(obj, dict):
            raise LLMBadOutput("expected a JSON object")
        missing = [k for k in schema.get("required", []) if k not in obj]
        if missing:
            raise LLMBadOutput(f"missing keys: {missing}")
        return obj

    def embed(self, text: str) -> list[float]:
        r = self._post("/api/embed", {"model": self.embed_model, "input": text[:4000], "keep_alive": "30m"})
        self._raise_for(r)
        return r.json()["embeddings"][0]

    def models(self) -> list[str]:
        try:
            r = self.client.get(f"{self.base_url}/api/tags", timeout=3)
        except httpx.HTTPError as e:
            raise LLMUnavailable(str(e)) from e
        return [m["name"] for m in r.json().get("models", [])]


def generate_with_fallback(
    llm: LLM,
    models: list[str],
    prompt: str,
    schema: dict,
    images: list[bytes] | None = None,
    on_fallback: Callable[[str, Exception], None] | None = None,
) -> tuple[dict, str]:
    """Try each model in order. Timeouts, bad output and server errors move to the next one."""
    last: LLMError | None = None
    for i, model in enumerate(models):
        try:
            return llm.generate_json(model, prompt, schema, images), model
        except LLMUnavailable:
            raise
        except LLMError as e:
            last = e
            if on_fallback and i + 1 < len(models):
                on_fallback(model, e)
    raise last or LLMError("no models given")
