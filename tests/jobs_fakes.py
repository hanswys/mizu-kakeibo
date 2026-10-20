"""Fictional messages, a scripted Ollama, and a scripted terminal for the job-tracker run tests.

The scripted Ollama answers the real `OllamaJobAnalyser`, so the missing-value guard, validation,
and the context budget run as they do against the pinned model.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx

from mizu_kakeibo.constants import INBOX_MESSAGE_CONTRACT, MODEL, MODEL_DIGEST, OLLAMA_VERSION
from mizu_kakeibo.jobs.analyser import OllamaJobAnalyser
from mizu_kakeibo.jobs.constants import JOB_ANALYSIS_OUTPUT_CONTRACT
from mizu_kakeibo.models import InboxMessage

START = datetime(2026, 5, 1, 2, 0, tzinfo=timezone.utc)
CAREERS = "Example Careers <careers@example.invalid>"
OVERFLOW = "overflow"
TIMEOUT = "timeout"


def received(day: int) -> datetime:
    return START + timedelta(days=day - 1)


def message(
    message_id: str, day: int, subject: str, text: str = "", sender: str = CAREERS
) -> InboxMessage:
    return InboxMessage.model_validate_json(
        json.dumps(
            {
                "contract_version": INBOX_MESSAGE_CONTRACT,
                "source": {"provider": "gmail", "message_id": message_id, "thread_id": "1a2b"},
                "received_at": received(day).isoformat(),
                "sender": sender,
                "subject": subject,
                "content": {
                    "text": text or f"An update about: {subject}",
                    "source_type": "text_plain",
                    "truncated": False,
                },
            }
        )
    )


def update(company, job_title, status, reason="Employer reports a status") -> str:
    return json.dumps(
        {
            "contract_version": JOB_ANALYSIS_OUTPUT_CONTRACT,
            "decision": "status_update",
            "reason": reason,
            "application": {"company": company, "job_title": job_title, "status": status},
            "missing_fields": [],
        }
    )


def other(decision, missing_fields=(), reason="No application status") -> str:
    return json.dumps(
        {
            "contract_version": JOB_ANALYSIS_OUTPUT_CONTRACT,
            "decision": decision,
            "reason": reason,
            "application": None,
            "missing_fields": list(missing_fields),
        }
    )


class ScriptedOllama:
    """Answer each chat with the content scripted for the message's subject."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": OLLAMA_VERSION})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": MODEL, "digest": MODEL_DIGEST}]})
        body = json.loads(request.content)
        subject = json.loads(body["messages"][1]["content"])["message"]["subject"]
        self.calls.append(subject)
        answer = self.answers[subject]
        if answer == TIMEOUT:
            raise httpx.ReadTimeout("scripted timeout", request=request)
        prompt_tokens = 20000 if answer == OVERFLOW else 900
        return httpx.Response(
            200,
            json={"message": {"content": answer}, "prompt_eval_count": prompt_tokens},
        )

    def analyser(self) -> OllamaJobAnalyser:
        client = httpx.Client(transport=httpx.MockTransport(self.handler), base_url="http://test")
        return OllamaJobAnalyser(client=client)


class FakeConsole:
    """Answer each prompt from a script; a callable answer runs first and returns the answer."""

    def __init__(self, answers, *, interactive=True) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.output: list[str] = []
        self.paged: list[str] = []
        self.opened: list[str] = []
        self.tty = interactive

    def interactive(self) -> bool:
        return self.tty

    def width(self) -> int:
        return 100

    def write(self, text: str = "") -> None:
        self.output.append(text)

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        return answer() if callable(answer) else answer

    def page(self, text: str) -> None:
        self.paged.append(text)

    def open(self, url: str) -> None:
        self.opened.append(url)

    @property
    def text(self) -> str:
        return "\n".join(self.output)
