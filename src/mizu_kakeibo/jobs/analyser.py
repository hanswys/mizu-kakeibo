from __future__ import annotations

from typing import Protocol

from ..ollama import OllamaRuntime, RuntimeMetadata
from .constants import JOBS_PROMPT_VERSION, JOBS_SYSTEM_PROMPT
from .models import JobAnalysisInput, JobAnalysisOutput


class JobAnalyser(Protocol):
    def analyse(self, analyser_input: JobAnalysisInput) -> JobAnalysisOutput: ...

    def verify_runtime(self) -> RuntimeMetadata: ...


class OllamaJobAnalyser(OllamaRuntime):
    """The job-application analyser on the expense app's pinned model and runtime."""

    prompt_version = JOBS_PROMPT_VERSION

    def analyse(self, analyser_input: JobAnalysisInput) -> JobAnalysisOutput:
        return self._chat(
            JOBS_SYSTEM_PROMPT,
            analyser_input.model_dump_json(),
            JobAnalysisOutput.model_json_schema(),
            JobAnalysisOutput.model_validate_json,
        )
