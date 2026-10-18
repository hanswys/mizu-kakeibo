from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from mizu_kakeibo.constants import MODEL, MODEL_DIGEST, OLLAMA_VERSION
from mizu_kakeibo.jobs.analyser import OllamaJobAnalyser
from mizu_kakeibo.jobs.constants import (
    GROUNDING_FAILURE_REASON,
    JOB_ANALYSIS_OUTPUT_CONTRACT,
    JOBS_PROMPT_VERSION,
    JOBS_SYSTEM_PROMPT,
    MISSING_VALUE_REASON,
)
from mizu_kakeibo.jobs.models import (
    JobAnalysisInput,
    JobAnalysisOutput,
    ground,
    review_missing_values,
)
from mizu_kakeibo.jobs.names import LEGAL_FORMS, company_key, fold_text, job_title_key
from mizu_kakeibo.models import AnalysisMessage
from mizu_kakeibo.ollama import ContextOverflowError, MessageAnalysisError, TokenUsage


@pytest.mark.parametrize(
    ("value", "folded"),
    [
        ("ＤＡＴＡ　Analyst", "data analyst"),
        ("Data​Analyst", "dataanalyst"),
        ("Macy’s Kitchen", "macys kitchen"),
        ("Dean's List", "deans list"),
        ("R&D Lead", "r and d lead"),
        ("Engineer (Backend) - Remote/Hybrid", "engineer backend remote hybrid"),
        ("  Software   Engineer  ", "software engineer"),
        ("STRASSE Café", "strasse café"),
        ("Straße", "strasse"),
    ],
)
def test_text_folding(value, folded) -> None:
    assert fold_text(value) == folded


@pytest.mark.parametrize(
    "value",
    [
        "Example Company Sdn. Bhd.",
        "EXAMPLE COMPANY SDN. BHD.",
        "example company sdn bhd",
        "Example Company",
        "Example Company (M) Sdn Bhd",
        "Example  Company Sendirian Berhad",
    ],
)
def test_contract_company_variants_share_one_key(value) -> None:
    assert company_key(value) == "example company"


@pytest.mark.parametrize("form", LEGAL_FORMS)
def test_every_legal_form_is_removed_after_a_word(form) -> None:
    assert company_key(f"Example {form}") == "example"


@pytest.mark.parametrize(
    ("value", "key"),
    [
        ("Berhad", "berhad"),
        ("Example Ltd Sdn Bhd", "example ltd"),
        ("Example Pte. Ltd.", "example"),
        ("Example Co., Ltd.", "example"),
        ("Example Co", "example co"),
        ("Incorporated Example", "incorporated example"),
        ("Examplebhd", "examplebhd"),
    ],
)
def test_legal_form_removal_is_one_trailing_whole_form(value, key) -> None:
    assert company_key(value) == key


def test_job_titles_keep_legal_words() -> None:
    assert job_title_key("Corporation Lawyer, Ltd.") == "corporation lawyer ltd"


def output(decision="status_update", application=None, missing_fields=None, **changes):
    if decision == "status_update" and application is None:
        application = {
            "company": "Example Company Sdn. Bhd.",
            "job_title": "Software Engineer",
            "status": "rejected",
        }
    value = {
        "contract_version": JOB_ANALYSIS_OUTPUT_CONTRACT,
        "decision": decision,
        "reason": "Employer declined the application",
        "application": application,
        "missing_fields": missing_fields or [],
        **changes,
    }
    return json.dumps(value)


def test_contract_output_example_is_valid() -> None:
    result = JobAnalysisOutput.model_validate_json(output())
    assert result.application is not None
    assert result.application.status.value == "rejected"


@pytest.mark.parametrize(
    "content",
    [
        output(decision="hired"),
        output(reason="x" * 201),
        output(
            decision="not_application",
            application={"company": "A", "job_title": "B", "status": "applied"},
        ),
        output(
            decision="needs_review",
            application={"company": "A", "job_title": "B", "status": "applied"},
        ),
        output(decision="not_application", application=None, missing_fields=["company"]),
        output(missing_fields=["company"]),
        output(decision="needs_review", missing_fields=["company", "company"]),
        output(decision="needs_review", missing_fields=["merchant"]),
        output(application={"company": " ", "job_title": "Engineer", "status": "applied"}),
        output(application={"company": "?!", "job_title": "Engineer", "status": "applied"}),
        output(application={"company": "Example", "job_title": "--", "status": "applied"}),
        output(application={"company": "E" * 201, "job_title": "Engineer", "status": "applied"}),
        output(application={"company": "Example", "job_title": "Engineer", "status": "withdrawn"}),
        output(application={"company": "Example", "job_title": "Engineer"}),
        output(extra="field"),
        output(contract_version="job-analysis-output/v1"),
    ],
)
def test_output_invariant_violations_are_invalid(content) -> None:
    with pytest.raises(ValidationError):
        JobAnalysisOutput.model_validate_json(content)


def test_needs_review_may_list_each_missing_value_once() -> None:
    fields = ["company", "job_title", "status", "message_content"]
    result = JobAnalysisOutput.model_validate_json(
        output(decision="needs_review", missing_fields=fields)
    )
    assert [field.value for field in result.missing_fields] == fields


@pytest.mark.parametrize(
    ("content", "missing"),
    [
        (
            output(
                application={"company": "Example", "job_title": "", "status": "applied"},
                missing_fields=["job_title"],
            ),
            ["job_title"],
        ),
        (
            output(application={"company": "Example", "job_title": "null", "status": "rejected"}),
            ["job_title"],
        ),
        (
            output(application={"company": " ", "job_title": "N/A", "status": "interview"}),
            ["company", "job_title"],
        ),
        (
            output(application={"company": "Example", "job_title": None, "status": "applied"}),
            ["job_title"],
        ),
        (output(missing_fields=["status"]), ["status"]),
        (
            output(
                decision="not_application",
                application=None,
                missing_fields=["company", "job_title"],
            ),
            ["company", "job_title"],
        ),
        (
            output(
                application={"company": "Unknown", "job_title": "", "status": "applied"},
                missing_fields=["job_title"],
            ),
            ["job_title", "company"],
        ),
    ],
)
def test_reported_missing_values_go_to_review(content, missing) -> None:
    result = review_missing_values(content)
    assert result is not None
    assert result.decision.value == "needs_review"
    assert result.application is None
    assert [field.value for field in result.missing_fields] == missing
    assert result.reason == MISSING_VALUE_REASON


@pytest.mark.parametrize(
    "content",
    [
        output(),
        output(decision="not_application", application=None),
        output(decision="needs_review", application=None, missing_fields=["job_title"]),
        output(decision="needs_review", missing_fields=["job_title"]),
        output(decision="hired", missing_fields=["company"]),
        "[]",
    ],
)
def test_missing_value_guard_leaves_other_outputs_alone(content) -> None:
    assert review_missing_values(content) is None


@pytest.mark.parametrize(
    "content",
    [
        output(missing_fields=["merchant"]),
        output(missing_fields=["company"], extra="field"),
        output(decision="not_application", application=None, missing_fields=["job_title"] * 2),
    ],
)
def test_guarded_outputs_must_still_meet_the_other_invariants(content) -> None:
    with pytest.raises(ValidationError):
        review_missing_values(content)


def analyser_input(subject="Your application", text="Body") -> JobAnalysisInput:
    return JobAnalysisInput(
        contract_version="job-analysis-input/v0",
        message=AnalysisMessage(
            received_at=datetime(2026, 5, 4, 1, 30, tzinfo=timezone.utc),
            sender="Example Careers <careers@example.invalid>",
            subject=subject,
            text=text,
            truncated=False,
        ),
    )


def update(company, job_title, status="applied"):
    return JobAnalysisOutput.model_validate_json(
        output(application={"company": company, "job_title": job_title, "status": status})
    )


@pytest.mark.parametrize(
    ("subject", "text"),
    [
        ("Your application to Software Engineer at Example Company Sdn. Bhd.", "Thanks"),
        ("Update", "EXAMPLE COMPANY SDN BHD has declined your Software  Engineer application."),
        ("Software Engineer", "From Example Company Sdn. Bhd."),
    ],
)
def test_grounded_names_keep_the_output(subject, text) -> None:
    original = update("Example Company Sdn. Bhd.", "Software Engineer")
    assert ground(analyser_input(subject, text), original) is original


@pytest.mark.parametrize(
    ("company", "job_title", "missing"),
    [
        ("Example Company Sdn. Bhd.", "Senior Software Engineer", ["job_title"]),
        ("Example Co", "Software Engineer", ["company"]),
        ("Invented Name", "Invented Title", ["company", "job_title"]),
        # A whole-word sequence: a name inside a longer word does not count.
        ("Example Comp", "Software Engineer", ["company"]),
    ],
)
def test_ungrounded_names_send_the_update_to_review(company, job_title, missing) -> None:
    original = update(company, job_title)
    result = ground(
        analyser_input(
            "Your application", "Example Company Sdn. Bhd. declined your Software Engineer role."
        ),
        original,
    )
    assert result.decision.value == "needs_review"
    assert result.application is None
    assert [field.value for field in result.missing_fields] == missing
    assert result.reason == GROUNDING_FAILURE_REASON
    assert original.application is not None


def test_names_split_between_subject_and_body_do_not_ground() -> None:
    result = ground(
        analyser_input("Software", "Engineer at Example Company"),
        update("Example Company", "Software Engineer"),
    )
    assert [field.value for field in result.missing_fields] == ["job_title"]


def test_grounding_leaves_other_decisions_alone() -> None:
    original = JobAnalysisOutput.model_validate_json(output(decision="not_application"))
    assert ground(analyser_input(), original) is original


def client_for(content, prompt_eval_count=900):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": OLLAMA_VERSION})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": MODEL, "digest": MODEL_DIGEST}]})
        if request.url.path == "/api/chat":
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "message": {"content": content},
                    "prompt_eval_count": prompt_eval_count,
                    "eval_count": 30,
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    return client, requests


def test_jobs_analyser_uses_its_prompt_and_schema_on_the_shared_runtime() -> None:
    client, requests = client_for(output())
    analyser = OllamaJobAnalyser(client=client)

    result = analyser.analyse(analyser_input())

    assert result.application is not None
    (body,) = requests
    assert body["model"] == MODEL
    assert body["options"] == {"temperature": 0, "seed": 42, "num_ctx": 12288, "num_predict": 512}
    assert (body["stream"], body["tools"], body["think"], body["keep_alive"]) == (
        False,
        [],
        False,
        "30s",
    )
    assert body["messages"][0] == {"role": "system", "content": JOBS_SYSTEM_PROMPT}
    user = json.loads(body["messages"][1]["content"])
    assert user["contract_version"] == "job-analysis-input/v0"
    assert set(user["message"]) == {"received_at", "sender", "subject", "text", "truncated"}
    assert body["format"] == JobAnalysisOutput.model_json_schema()
    assert body["format"]["additionalProperties"] is False
    assert analyser.verify_runtime().prompt_version == JOBS_PROMPT_VERSION
    assert analyser.last_usage == TokenUsage(900, 30)


def test_jobs_prompt_is_the_contract_prompt_verbatim() -> None:
    contract = (Path(__file__).parents[1] / "docs/jobs/job-tracker-contract.md").read_text("utf-8")
    prompt = contract.split("The Version 0 system prompt is:\n\n```text\n", 1)[1]
    assert prompt.split("\n```", 1)[0] == JOBS_SYSTEM_PROMPT
    assert f"| Prompt version | `{JOBS_PROMPT_VERSION}` |" in contract


def test_jobs_analyser_sends_reported_missing_values_to_review() -> None:
    content = output(
        application={"company": "Example", "job_title": "", "status": "applied"},
        missing_fields=["job_title"],
    )
    client, _ = client_for(content)
    analyser = OllamaJobAnalyser(client=client)

    result = analyser.analyse(analyser_input())

    assert result.decision.value == "needs_review"
    assert analyser.last_original_output == content


def test_jobs_analyser_keeps_no_original_for_an_unguarded_output() -> None:
    client, _ = client_for(output())
    analyser = OllamaJobAnalyser(client=client)
    analyser.last_original_output = "stale"

    analyser.analyse(analyser_input())

    assert analyser.last_original_output is None


def test_jobs_analyser_rejects_invalid_output() -> None:
    client, _ = client_for(output(missing_fields=["merchant"]))
    with pytest.raises(MessageAnalysisError, match="output contract"):
        OllamaJobAnalyser(client=client).analyse(analyser_input())


def test_jobs_analyser_applies_the_context_budget() -> None:
    client, _ = client_for(output(), prompt_eval_count=12288 - 511)
    with pytest.raises(ContextOverflowError):
        OllamaJobAnalyser(client=client).analyse(analyser_input())
