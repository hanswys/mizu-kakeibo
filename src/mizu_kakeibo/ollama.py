from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from .constants import (
    CONTEXT_SIZE,
    MAX_GENERATED_TOKENS,
    MODEL,
    MODEL_DIGEST,
    OLLAMA_BASE_URL,
    OLLAMA_VERSION,
    PROMPT_VERSION,
    REQUEST_TIMEOUT_SECONDS,
    SEED,
    SYSTEM_PROMPT,
    TEMPERATURE,
)
from .models import ExpenseAnalysisInput, ExpenseAnalysisOutput


class AnalysisError(RuntimeError):
    """An operational analyser failure that must remain retryable."""


class ContextOverflowError(AnalysisError):
    """The prompt did not fit the analyser context budget, so the output is untrusted."""


@dataclass(frozen=True)
class RuntimeMetadata:
    runtime: str
    runtime_version: str
    model: str
    model_digest: str
    prompt_version: str
    temperature: int
    seed: int
    context_size: int
    max_generated_tokens: int


class OllamaAnalyser:
    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        *,
        client: httpx.Client | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)
        self._metadata: RuntimeMetadata | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OllamaAnalyser:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _expected_digest(value: str) -> str:
        return value.removeprefix("sha256-").removeprefix("sha256:")

    def verify_runtime(self) -> RuntimeMetadata:
        if self._metadata is not None:
            return self._metadata
        try:
            version_response = self._client.get("/api/version")
            version_response.raise_for_status()
            runtime_version = version_response.json().get("version")
            tags_response = self._client.get("/api/tags")
            tags_response.raise_for_status()
            models = tags_response.json().get("models", [])
            show_response = self._client.post("/api/show", json={"model": MODEL})
            show_response.raise_for_status()
            model_details = show_response.json()
        except (httpx.HTTPError, json.JSONDecodeError, AttributeError, TypeError) as error:
            raise AnalysisError(f"cannot verify Ollama runtime: {error}") from error

        if runtime_version != OLLAMA_VERSION:
            raise AnalysisError(
                f"Ollama version mismatch: expected {OLLAMA_VERSION}, found {runtime_version!r}"
            )
        installed = next(
            (
                entry
                for entry in models
                if isinstance(entry, dict) and MODEL in {entry.get("name"), entry.get("model")}
            ),
            None,
        )
        if installed is None:
            raise AnalysisError(f"required model is not installed: {MODEL}")
        if not isinstance(model_details, dict):
            raise AnalysisError(f"cannot inspect required model: {MODEL}")
        modelfile = model_details.get("modelfile")
        digest_match = (
            re.search(r"(?m)^FROM (?:.*[/\\])?sha256[-:]([0-9a-f]{64})\s*$", modelfile)
            if isinstance(modelfile, str)
            else None
        )
        digest = digest_match.group(1) if digest_match else None
        if digest != self._expected_digest(MODEL_DIGEST):
            raise AnalysisError(
                f"model digest mismatch for {MODEL}; the contract requires {MODEL_DIGEST}"
            )
        self._metadata = RuntimeMetadata(
            runtime="ollama",
            runtime_version=runtime_version,
            model=MODEL,
            model_digest=MODEL_DIGEST,
            prompt_version=PROMPT_VERSION,
            temperature=TEMPERATURE,
            seed=SEED,
            context_size=CONTEXT_SIZE,
            max_generated_tokens=MAX_GENERATED_TOKENS,
        )
        return self._metadata

    def analyse(self, analyser_input: ExpenseAnalysisInput) -> ExpenseAnalysisOutput:
        self.verify_runtime()
        request: dict[str, Any] = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": analyser_input.model_dump_json(),
                },
            ],
            "format": ExpenseAnalysisOutput.model_json_schema(),
            "options": {
                "temperature": TEMPERATURE,
                "seed": SEED,
                "num_ctx": CONTEXT_SIZE,
                "num_predict": MAX_GENERATED_TOKENS,
            },
            "stream": False,
            "tools": [],
        }
        try:
            response = self._client.post("/api/chat", json=request)
            response.raise_for_status()
            body = response.json()
            prompt_tokens = body.get("prompt_eval_count")
            if (
                not isinstance(prompt_tokens, int)
                or prompt_tokens + MAX_GENERATED_TOKENS > CONTEXT_SIZE
            ):
                raise ContextOverflowError("prompt exceeded the analyser context budget")
            content = body["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message.content is not a string")
            return ExpenseAnalysisOutput.model_validate_json(content)
        except (
            httpx.HTTPError,
            json.JSONDecodeError,
            AttributeError,
            KeyError,
            TypeError,
            ValidationError,
        ) as error:
            raise AnalysisError(
                "analyser returned malformed JSON or violated the output contract"
            ) from error
