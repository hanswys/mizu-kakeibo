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
from mizu_kakeibo.ollama import (
    AnalysisError,
    ContextOverflowError,
    ModelMemory,
    OllamaAnalyser,
    TokenUsage,
)

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
            return httpx.Response(
                200, json={"models": [{"name": model, "digest": digest.removeprefix("sha256-")}]}
            )
        if request.url.path == "/api/chat":
            body = json.loads(request.content)
            assert body["model"] == MODEL
            assert body["stream"] is False
            assert body["tools"] == []
            assert body["think"] is False
            assert body["keep_alive"] == "30s"
            assert body["options"] == {
                "temperature": 0,
                "seed": 42,
                "num_ctx": 12288,
                "num_predict": 512,
            }
            assert body["format"]["additionalProperties"] is False
            user_message = json.loads(body["messages"][1]["content"])
            assert "source" not in user_message["message"]
            response = {"message": {"content": content}, "eval_count": 40}
            if prompt_eval_count is not _ABSENT:
                response["prompt_eval_count"] = prompt_eval_count
            return httpx.Response(200, json=response)
        if request.url.path == "/api/ps":
            return httpx.Response(
                200, json={"models": [{"name": model, "size": 4 * 2**30, "size_vram": 3 * 2**30}]}
            )
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
    with pytest.raises(ContextOverflowError, match="context budget"):
        analyser.analyse(ExpenseAnalysisInput.from_inbox_message(inbox_message))


def test_reports_token_usage_and_loaded_model_memory(inbox_message) -> None:
    analyser = OllamaAnalyser(client=client_for(content=expense_content()))
    analyser.analyse(ExpenseAnalysisInput.from_inbox_message(inbox_message))

    assert analyser.last_usage == TokenUsage(prompt_tokens=900, generated_tokens=40)
    assert analyser.loaded_model_memory() == ModelMemory(4 * 2**30, 3 * 2**30)
    assert OllamaAnalyser(client=client_for(model="other")).loaded_model_memory() is None


def test_call_without_a_response_clears_previous_token_usage(inbox_message) -> None:
    analyser = OllamaAnalyser(client=client_for(content=expense_content()))
    analyser.analyse(ExpenseAnalysisInput.from_inbox_message(inbox_message))

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    analyser._client = httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://test")
    with pytest.raises(AnalysisError):
        analyser.analyse(ExpenseAnalysisInput.from_inbox_message(inbox_message))
    assert analyser.last_usage is None


def expense_input(inbox_message, text):
    message = inbox_message.model_copy(
        update={"content": inbox_message.content.model_copy(update={"text": text})}
    )
    return ExpenseAnalysisInput.from_inbox_message(message)


def test_bare_dollar_expense_reported_as_myr_goes_to_review(inbox_message) -> None:
    analyser = OllamaAnalyser(client=client_for(content=expense_content()))

    result = analyser.analyse(expense_input(inbox_message, "Receipt: $42.50 paid on 2026-10-06."))

    assert result.decision.value == "needs_review"
    assert result.transaction is None
    assert [field.value for field in result.missing_fields] == ["currency"]


@pytest.mark.parametrize(
    "text",
    [
        "Paid RM42.50 on 2026-10-06.",
        "Total $42.50 (MYR) paid on 2026-10-06.",
        "Paid 42.50 on 2026-10-06.",
    ],
)
def test_myr_expense_with_ringgit_or_no_marker_is_kept(inbox_message, text) -> None:
    analyser = OllamaAnalyser(client=client_for(content=expense_content()))

    result = analyser.analyse(expense_input(inbox_message, text))

    assert result.decision.value == "expense"


@pytest.mark.parametrize(
    "text",
    ["Paid $42.50 USD on 2026-10-06.", "Paid US$42.50 on 2026-10-06."],
)
def test_foreign_currency_stated_in_the_message_is_kept(inbox_message, text) -> None:
    content = json.loads(expense_content())
    content["transaction"]["currency"] = "USD"
    analyser = OllamaAnalyser(client=client_for(content=json.dumps(content)))

    result = analyser.analyse(expense_input(inbox_message, text))

    assert result.transaction is not None
    assert result.transaction.currency == "USD"


@pytest.mark.parametrize(
    "text",
    ["Paid $42.50 on 2026-10-06.", "Paid 42.50 on 2026-10-06."],
)
def test_foreign_currency_not_stated_in_the_message_goes_to_review(inbox_message, text) -> None:
    content = json.loads(expense_content())
    content["transaction"]["currency"] = "USD"
    analyser = OllamaAnalyser(client=client_for(content=json.dumps(content)))

    result = analyser.analyse(expense_input(inbox_message, text))

    assert result.decision.value == "needs_review"
    assert [field.value for field in result.missing_fields] == ["currency"]
