from __future__ import annotations

import json
from datetime import date
from itertools import count

import httpx
import pytest
from pydantic import ValidationError

from mizu_kakeibo import cli
from mizu_kakeibo.constants import ANALYSIS_OUTPUT_CONTRACT, PROMPT_VERSION
from mizu_kakeibo.evaluation import evaluate, load_dataset
from mizu_kakeibo.models import AnalysisDecision, ExpenseAnalysisOutput, Transaction
from mizu_kakeibo.ollama import (
    AnalysisError,
    ContextOverflowError,
    ModelMemory,
    RuntimeMetadata,
    TokenUsage,
)

METADATA = RuntimeMetadata(
    runtime="ollama",
    runtime_version="0.40.5",
    model="qwen3.5:4b-q4_K_M",
    model_digest="sha256-test",
    prompt_version=PROMPT_VERSION,
    temperature=0,
    seed=42,
    context_size=32768,
    max_generated_tokens=512,
)


def output(decision, merchant="Example Store", amount=4250):
    transaction = None
    if decision == "expense":
        transaction = Transaction(
            merchant=merchant,
            amount_minor=amount,
            currency="MYR",
            transaction_date=date(2026, 10, 6),
        )
    return ExpenseAnalysisOutput(
        contract_version=ANALYSIS_OUTPUT_CONTRACT,
        decision=AnalysisDecision(decision),
        reason="Fictional evaluation output",
        transaction=transaction,
        missing_fields=[],
    )


def invalid_output():
    error = AnalysisError("analyser returned malformed JSON or violated the output contract")
    error.__cause__ = json.JSONDecodeError("bad", "", 0)
    return error


def operational_failure():
    error = AnalysisError("analyser returned malformed JSON or violated the output contract")
    error.__cause__ = httpx.ConnectError("refused")
    return error


class ScriptedAnalyser:
    """Returns scripted results per message ID, cycling through them across runs."""

    def __init__(self, script):
        self.script = {message_id: list(results) for message_id, results in script.items()}
        self.calls = 0

    def analyse(self, analyser_input):
        self.calls += 1
        subject = analyser_input.message.subject
        result = self.script[subject].pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def verify_runtime(self):
        return METADATA


def example(inbox_message, message_id, decision, merchant="Example Store", amount=4250):
    message = inbox_message.model_dump(mode="json")
    message["source"]["message_id"] = message_id
    message["subject"] = message_id
    transaction = None
    if decision == "expense":
        transaction = {
            "merchant": merchant,
            "amount_minor": amount,
            "currency": "MYR",
            "transaction_date": "2026-10-06",
        }
    return {"message": message, "expected": {"decision": decision, "transaction": transaction}}


def dataset(*examples, prompt_version=PROMPT_VERSION):
    return {
        "dataset_version": "fictional-1",
        "prompt_version": prompt_version,
        "examples": list(examples),
    }


def ticking_clock():
    ticks = count()
    return lambda: next(ticks) / 10


def test_scores_decisions_fields_and_consistency(inbox_message):
    data = load_dataset(
        dataset(
            example(inbox_message, "expense-ok", "expense"),
            example(inbox_message, "expense-wrong-amount", "expense"),
            example(inbox_message, "expense-missed", "expense"),
            example(inbox_message, "plain-mail", "not_expense"),
            example(inbox_message, "refund", "needs_review"),
            example(inbox_message, "newsletter", "not_expense"),
        )
    )
    analyser = ScriptedAnalyser(
        {
            "expense-ok": [output("expense", merchant="  example   STORE "), output("expense")],
            "expense-wrong-amount": [output("expense", amount=425), output("expense", amount=425)],
            "expense-missed": [output("not_expense"), output("needs_review")],
            "plain-mail": [output("expense"), invalid_output()],
            "refund": [output("needs_review"), output("not_expense")],
            "newsletter": [output("needs_review"), operational_failure()],
        }
    )

    report = evaluate(data, analyser, runs=2, clock=ticking_clock())
    summary = report["summary"]
    first, second = summary["runs"]

    assert analyser.calls == 12
    assert first["correct_decisions"] == 3
    assert first["false_expenses"] == 1
    assert first["missed_expenses"] == 1
    assert first["correct_reviews"] == 1
    assert first["unnecessary_reviews"] == 1
    assert first["extracted_expenses"] == 2
    assert first["field_accuracy"] == {
        "merchant": 2,
        "amount_minor": 1,
        "currency": 2,
        "transaction_date": 2,
    }
    assert first["exact_transactions"] == 1
    assert second["expenses_sent_to_review"] == 1
    assert second["missed_reviews"] == 1
    assert second["invalid_outputs"] == 1
    assert second["operational_failures"] == 1
    assert summary["consistency"] == {
        "examples": 6,
        "decision_consistent": 2,
        "output_consistent": 1,
    }
    assert summary["latency_ms"]["min"] == pytest.approx(100.0)
    assert summary["metadata"]["prompt_version"] == PROMPT_VERSION
    assert summary["metadata"]["runs"] == 2
    assert summary["error_categories"]["amount_minor_mismatch"] == 1
    assert report["examples"][3]["observed"] == ["expense", "invalid_output"]
    assert report["examples"][3]["error_categories"] == ["false_expense", "invalid_output"]


def test_reports_token_usage_and_model_memory_when_available(inbox_message):
    data = load_dataset(
        dataset(
            example(inbox_message, "a", "not_expense"),
            example(inbox_message, "b", "not_expense"),
        )
    )
    usages = iter([TokenUsage(400, 30), TokenUsage(1200, 50)])

    class MeasuredAnalyser(ScriptedAnalyser):
        last_usage = None

        def analyse(self, analyser_input):
            result = super().analyse(analyser_input)
            self.last_usage = next(usages)
            return result

        def loaded_model_memory(self):
            return ModelMemory(total_bytes=12 * 2**30, gpu_bytes=9 * 2**30)

    analyser = MeasuredAnalyser({"a": [output("not_expense")], "b": [output("not_expense")]})

    summary = evaluate(data, analyser)["summary"]

    assert summary["tokens"] == {
        "measured_calls": 2,
        "prompt": {"median": 800, "max": 1200},
        "generated": {"median": 40, "max": 50},
    }
    assert summary["model_memory"] == {"total_gib": 12.0, "gpu_fraction": 0.75}


def test_resource_usage_is_null_when_the_analyser_cannot_report_it(inbox_message):
    data = load_dataset(dataset(example(inbox_message, "a", "not_expense")))

    summary = evaluate(data, ScriptedAnalyser({"a": [output("not_expense")]}))["summary"]

    assert summary["tokens"] is None
    assert summary["model_memory"] is None


def test_context_overflow_is_counted_separately(inbox_message):
    data = load_dataset(dataset(example(inbox_message, "long-mail", "not_expense")))
    analyser = ScriptedAnalyser(
        {"long-mail": [ContextOverflowError("prompt exceeded the analyser context budget")]}
    )

    report = evaluate(data, analyser)

    run = report["summary"]["runs"][0]
    assert run["context_overflows"] == 1
    assert run["invalid_outputs"] == 0
    assert run["operational_failures"] == 0
    assert report["examples"][0]["error_categories"] == ["context_overflow"]


def test_summary_excludes_message_ids_and_model_reasons(inbox_message):
    data = load_dataset(dataset(example(inbox_message, "private-id-123", "not_expense")))
    analyser = ScriptedAnalyser({"private-id-123": [output("not_expense")]})

    report = evaluate(data, analyser)

    rendered = json.dumps(report["summary"])
    assert "private-id-123" not in rendered
    assert "Fictional evaluation output" not in rendered


def test_dataset_rejects_ground_truth_for_another_prompt(inbox_message):
    with pytest.raises(ValueError, match="recorded for prompt"):
        load_dataset(
            dataset(example(inbox_message, "a", "not_expense"), prompt_version="expense-v0.1")
        )


def test_dataset_rejects_inconsistent_ground_truth(inbox_message):
    bad = example(inbox_message, "a", "needs_review")
    bad["expected"]["transaction"] = example(inbox_message, "a", "expense")["expected"][
        "transaction"
    ]
    with pytest.raises(ValidationError):
        load_dataset(dataset(bad))


def test_dataset_rejects_duplicate_message_ids(inbox_message):
    with pytest.raises(ValidationError, match="unique"):
        load_dataset(
            dataset(
                example(inbox_message, "a", "not_expense"),
                example(inbox_message, "a", "not_expense"),
            )
        )


def test_evaluate_command_writes_private_report_and_prints_summary(
    tmp_path, monkeypatch, capsys, inbox_message
):
    dataset_path = tmp_path / "private-eval.json"
    report_path = tmp_path / "private-report.json"
    dataset_path.write_text(
        json.dumps(dataset(example(inbox_message, "private-id", "expense"))), encoding="utf-8"
    )
    analyser = ScriptedAnalyser({"private-id": [output("expense")] * 3})

    class Context:
        def __enter__(self):
            return analyser

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(cli, "OllamaAnalyser", Context)

    assert cli.main(["evaluate", str(dataset_path), "--output", str(report_path)]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["runs"][0]["exact_transactions"] == 1
    assert summary["consistency"]["output_consistent"] == 1
    assert "private-id" not in json.dumps(summary)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["examples"][0]["message_id"] == "private-id"
    assert not any(tmp_path.glob("*.sqlite3"))


def test_reports_purchases_membership_by_expected_decision(inbox_message):
    in_purchases = example(inbox_message, "receipt", "expense")
    in_purchases["gmail_purchases"] = True
    bank_alert = example(inbox_message, "bank-alert", "expense")
    bank_alert["gmail_purchases"] = False
    newsletter = example(inbox_message, "newsletter", "not_expense")
    newsletter["gmail_purchases"] = False
    data = load_dataset(
        dataset(in_purchases, bank_alert, newsletter, example(inbox_message, "x", "needs_review"))
    )
    analyser = ScriptedAnalyser(
        {
            "receipt": [output("expense")],
            "bank-alert": [output("expense")],
            "newsletter": [output("not_expense")],
            "x": [output("needs_review")],
        }
    )

    report = evaluate(data, analyser)

    assert report["summary"]["purchases_membership"] == {
        "expense": {"in_purchases": 1, "outside_purchases": 1, "unknown": 0},
        "not_expense": {"in_purchases": 0, "outside_purchases": 1, "unknown": 0},
        "needs_review": {"in_purchases": 0, "outside_purchases": 0, "unknown": 1},
    }
    assert [entry["gmail_purchases"] for entry in report["examples"]] == [True, False, False, None]


def test_purchases_membership_is_null_when_not_recorded(inbox_message):
    data = load_dataset(dataset(example(inbox_message, "a", "not_expense")))

    summary = evaluate(data, ScriptedAnalyser({"a": [output("not_expense")]}))["summary"]

    assert summary["purchases_membership"] is None


def test_dataset_rejects_unknown_example_fields(inbox_message):
    bad = example(inbox_message, "a", "not_expense")
    bad["purchases"] = True
    with pytest.raises(ValidationError):
        load_dataset(dataset(bad))
