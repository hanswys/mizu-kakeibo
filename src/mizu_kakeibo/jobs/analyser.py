from __future__ import annotations

from typing import Protocol

from ..ollama import OllamaRuntime, RuntimeMetadata
from .constants import JOBS_PROMPT_VERSION, JOBS_SYSTEM_PROMPT
from .models import JobAnalysisInput, JobAnalysisOutput, review_missing_values


class JobAnalyser(Protocol):
    def analyse(self, analyser_input: JobAnalysisInput) -> JobAnalysisOutput: ...

    def verify_runtime(self) -> RuntimeMetadata: ...


class OllamaJobAnalyser(OllamaRuntime):
    """The job-application analyser on the expense app's pinned model and runtime."""

    prompt_version = JOBS_PROMPT_VERSION
    last_original_output: str | None = None

    def analyse(self, analyser_input: JobAnalysisInput) -> JobAnalysisOutput:
        """Analyse one message; `last_original_output` keeps an output the guard replaced."""
        self.last_original_output = None

        def parse(content: str) -> JobAnalysisOutput:
            reviewed = review_missing_values(content)
            if reviewed is None:
                return JobAnalysisOutput.model_validate_json(content)
            self.last_original_output = content
            return reviewed

        return self._chat(
            JOBS_SYSTEM_PROMPT,
            analyser_input.model_dump_json(),
            JobAnalysisOutput.model_json_schema(),
            parse,
        )
