from __future__ import annotations

import json

import httpx
import pytest

from mizu_kakeibo.constants import (
    CONTEXT_SIZE,
    MAX_GENERATED_TOKENS,
    MODEL,
    MODEL_DIGEST,
    OLLAMA_VERSION,
)
from mizu_kakeibo.models import ExpenseAnalysisInput
from mizu_kakeibo.ollama import AnalysisError, OllamaAnalyser

_ABSENT = object()


def client_for(
    *,
    version=OLLAMA_VERSION,
    model=MODEL,
    digest=MODEL_DIGEST,
    content=None,
    prompt_eval_count=900,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": version})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": model, "digest": "manifest"}]})
        if request.url.path == "/api/show":
            body = json.loads(request.content)
            assert body == {"model": MODEL}
            return httpx.Response(
                200,
                json={"modelfile": f"FROM /models/blobs/{digest}"},
            )
        if request.url.path == "/api/chat":
            body = json.loads(request.content)
            assert body["model"] == MODEL
            assert body["stream"] is False
            assert body["tools"] == []
            assert body["options"] == {
                "temperature": 0,
                "seed": 42,
                "num_ctx": 12288,
                "num_predict": 512,
            }
            assert body["format"]["additionalProperties"] is False
            user_message = json.loads(body["messages"][1]["content"])
            assert "source" not in user_message["message"]
            response = {"message": {"content": content}}
            if prompt_eval_count is not _ABSENT:
                response["prompt_eval_count"] = prompt_eval_count
            return httpx.Response(200, json=response)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")


def expense_content() -> str:
    return json.dumps(
        {
            "contract_version": "expense-analysis-output/v0",
            "decision": "expense",
            "reason": "Completed purchase with required fields",
            "transaction": {
                "merchant": "Example Store",
                "amount_minor": 4250,
                "currency": "MYR",
                "transaction_date": "2026-10-06",
            },
            "missing_fields": [],
        }
    )


def test_calls_schema_constrained_chat_and_validates_output(inbox_message) -> None:
    analyser = OllamaAnalyser(client=client_for(content=expense_content()))
    result = analyser.analyse(ExpenseAnalysisInput.from_inbox_message(inbox_message))
    assert result.transaction is not None
    assert result.transaction.amount_minor == 4250


@pytest.mark.parametrize(
    "client",
    [
        client_for(version="0.99.0"),
        client_for(model="another-model"),
        client_for(digest="wrong-digest"),
    ],
)
def test_rejects_runtime_or_model_drift(client: httpx.Client) -> None:
    with pytest.raises(AnalysisError):
        OllamaAnalyser(client=client).verify_runtime()


def test_malformed_model_output_is_operational_failure(inbox_message) -> None:
    analyser = OllamaAnalyser(client=client_for(content="not JSON"))
    with pytest.raises(AnalysisError) as error:
        analyser.analyse(ExpenseAnalysisInput.from_inbox_message(inbox_message))
    assert "not JSON" not in str(error.value)
    assert "output contract" in str(error.value)


def test_prompt_that_fills_the_reply_budget_is_accepted(inbox_message) -> None:
    analyser = OllamaAnalyser(
        client=client_for(
            content=expense_content(),
            prompt_eval_count=CONTEXT_SIZE - MAX_GENERATED_TOKENS,
        )
    )
    assert analyser.analyse(ExpenseAnalysisInput.from_inbox_message(inbox_message)).transaction


@pytest.mark.parametrize(
    "prompt_eval_count",
    [CONTEXT_SIZE - MAX_GENERATED_TOKENS + 1, CONTEXT_SIZE, _ABSENT, None],
)
def test_truncated_or_unmeasured_prompt_discards_valid_output(
    inbox_message, prompt_eval_count
) -> None:
    analyser = OllamaAnalyser(
        client=client_for(content=expense_content(), prompt_eval_count=prompt_eval_count)
    )
    with pytest.raises(AnalysisError, match="context budget"):
        analyser.analyse(ExpenseAnalysisInput.from_inbox_message(inbox_message))
