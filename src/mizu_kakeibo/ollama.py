from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import ValidationError

from .constants import (
    ANALYSIS_OUTPUT_CONTRACT,
    CONTEXT_SIZE,
    KEEP_ALIVE,
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
from .models import AnalysisDecision, ExpenseAnalysisInput, ExpenseAnalysisOutput, MissingField

_Output = TypeVar("_Output")
_Runtime = TypeVar("_Runtime", bound="OllamaRuntime")

_DOLLAR_AMOUNT = re.compile(r"\$\s?\d")
_RINGGIT_MARKER = re.compile(r"(?<![A-Za-z])(?:RM|MYR)(?![A-Za-z])|ringgit", re.IGNORECASE)
_CURRENCY_MARKERS = {"USD": ("US$", "US dollar"), "EUR": ("€",), "GBP": ("£",)}


def _shows_currency(text: str, currency: str) -> bool:
    if currency == "MYR":
        return bool(_RINGGIT_MARKER.search(text)) or not _DOLLAR_AMOUNT.search(text)
    if re.search(rf"(?<![A-Za-z]){currency}(?![A-Za-z])", text):
        return True
    folded = text.casefold()
    return any(marker.casefold() in folded for marker in _CURRENCY_MARKERS.get(currency, ()))


def enforce_currency_evidence(
    analyser_input: ExpenseAnalysisInput, output: ExpenseAnalysisOutput
) -> ExpenseAnalysisOutput:
    """Send an expense to review when the message does not show the reported currency."""
    transaction = output.transaction
    if transaction is None:
        return output
    message = analyser_input.message
    if _shows_currency(f"{message.subject}\n{message.text}", transaction.currency):
        return output
    return ExpenseAnalysisOutput(
        contract_version=ANALYSIS_OUTPUT_CONTRACT,
        decision=AnalysisDecision.NEEDS_REVIEW,
        reason="The message does not state the currency of the amount",
        transaction=None,
        missing_fields=[MissingField.CURRENCY],
    )


class AnalysisError(RuntimeError):
    """An operational analyser failure that must remain retryable."""


class ContextOverflowError(AnalysisError):
    """The prompt did not fit the analyser context budget, so the output is untrusted."""


class MessageAnalysisError(AnalysisError):
    """The analyser timed out or returned invalid output for this message."""


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    generated_tokens: int


@dataclass(frozen=True)
class ModelMemory:
    total_bytes: int
    gpu_bytes: int


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


class OllamaRuntime:
    """The pinned Ollama runtime shared by the expense and job-tracker analysers.

    A subclass sets its prompt version and calls `_chat` with its own prompt, output schema,
    and validation; the model, runtime checks, options, and context budget stay shared.
    """

    prompt_version: str

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
        self.last_usage: TokenUsage | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self: _Runtime) -> _Runtime:
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
        digest = installed.get("digest")
        if not isinstance(digest, str) or (
            self._expected_digest(digest) != self._expected_digest(MODEL_DIGEST)
        ):
            raise AnalysisError(
                f"model digest mismatch for {MODEL}; the contract requires {MODEL_DIGEST}"
            )
        self._metadata = RuntimeMetadata(
            runtime="ollama",
            runtime_version=runtime_version,
            model=MODEL,
            model_digest=MODEL_DIGEST,
            prompt_version=self.prompt_version,
            temperature=TEMPERATURE,
            seed=SEED,
            context_size=CONTEXT_SIZE,
            max_generated_tokens=MAX_GENERATED_TOKENS,
        )
        return self._metadata

    def loaded_model_memory(self) -> ModelMemory | None:
        """Report how much memory the loaded model uses, or None if it is not loaded."""
        try:
            response = self._client.get("/api/ps")
            response.raise_for_status()
            models = response.json().get("models", [])
        except (httpx.HTTPError, json.JSONDecodeError, AttributeError) as error:
            raise AnalysisError(f"cannot read loaded model memory: {error}") from error
        for entry in models:
            if isinstance(entry, dict) and MODEL in {entry.get("name"), entry.get("model")}:
                total, gpu = entry.get("size"), entry.get("size_vram")
                if isinstance(total, int) and isinstance(gpu, int):
                    return ModelMemory(total_bytes=total, gpu_bytes=gpu)
        return None

    def _chat(
        self,
        system_prompt: str,
        user_content: str,
        output_schema: dict[str, Any],
        parse: Callable[[str], _Output],
    ) -> _Output:
        """Run one schema-constrained chat and parse its content, mapping failures to errors."""
        self.verify_runtime()
        self.last_usage = None
        request: dict[str, Any] = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "format": output_schema,
            "options": {
                "temperature": TEMPERATURE,
                "seed": SEED,
                "num_ctx": CONTEXT_SIZE,
                "num_predict": MAX_GENERATED_TOKENS,
            },
            "stream": False,
            "tools": [],
            "think": False,
            "keep_alive": KEEP_ALIVE,
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
            generated_tokens = body.get("eval_count")
            if isinstance(generated_tokens, int):
                self.last_usage = TokenUsage(prompt_tokens, generated_tokens)
            content = body["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message.content is not a string")
            return parse(content)
        except httpx.ConnectTimeout as error:
            raise AnalysisError(f"cannot reach the analyser runtime: {error}") from error
        except httpx.TimeoutException as error:
            raise MessageAnalysisError("analyser timed out") from error
        except httpx.HTTPError as error:
            raise AnalysisError(f"analyser runtime request failed: {error}") from error
        except (
            json.JSONDecodeError,
            AttributeError,
            KeyError,
            TypeError,
            ValidationError,
        ) as error:
            raise MessageAnalysisError(
                "analyser returned malformed JSON or violated the output contract"
            ) from error


class OllamaAnalyser(OllamaRuntime):
    prompt_version = PROMPT_VERSION

    def analyse(self, analyser_input: ExpenseAnalysisInput) -> ExpenseAnalysisOutput:
        def parse(content: str) -> ExpenseAnalysisOutput:
            output = ExpenseAnalysisOutput.model_validate_json(content)
            return enforce_currency_evidence(analyser_input, output)

        return self._chat(
            SYSTEM_PROMPT,
            analyser_input.model_dump_json(),
            ExpenseAnalysisOutput.model_json_schema(),
            parse,
        )
