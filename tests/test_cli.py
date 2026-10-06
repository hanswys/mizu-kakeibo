from __future__ import annotations

import json
from datetime import date

from mizu_kakeibo import cli
from mizu_kakeibo.constants import ANALYSIS_OUTPUT_CONTRACT
from mizu_kakeibo.models import AnalysisDecision, ExpenseAnalysisOutput, Transaction
from mizu_kakeibo.ollama import RuntimeMetadata


class FakeOllamaAnalyser:
    def __init__(self) -> None:
        self.output = ExpenseAnalysisOutput(
            contract_version=ANALYSIS_OUTPUT_CONTRACT,
            decision=AnalysisDecision.EXPENSE,
            reason="Completed fictional purchase",
            transaction=Transaction(
                merchant="Example Store",
                amount_minor=4250,
                currency="MYR",
                transaction_date=date(2026, 10, 6),
            ),
            missing_fields=[],
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def analyse(self, _):
        return self.output

    def verify_runtime(self):
        return RuntimeMetadata(
            runtime="ollama",
            runtime_version="0.23.1",
            model="qwen2.5:14b",
            model_digest="sha256-test",
            prompt_version="expense-v0.1",
            temperature=0,
            seed=42,
            context_size=32768,
            max_generated_tokens=512,
        )


def gmail_message():
    return {
        "id": "message-1",
        "thread_id": "thread-1",
        "internal_date": "1791250200000",
        "payload": {
            "mime_type": "text/plain",
            "filename": "",
            "headers": [
                {"name": "From", "value": "Example Store <receipts@example.invalid>"},
                {"name": "Subject", "value": "Receipt"},
            ],
            "body": {
                "content": "Purchase completed for MYR 42.50 on 2026-10-06.",
                "attachment_id": None,
            },
            "parts": None,
        },
    }


def test_process_accepts_raw_gmail_message_and_filter_new_skips_it(
    tmp_path, monkeypatch, capsys
) -> None:
    source = tmp_path / "gmail.json"
    source.write_text(json.dumps(gmail_message()), encoding="utf-8")
    database = tmp_path / "private.sqlite3"
    ledger = tmp_path / "ledger.csv"
    monkeypatch.setattr(cli, "OllamaAnalyser", FakeOllamaAnalyser)

    assert (
        cli.main(
            [
                "process",
                str(source),
                "--database",
                str(database),
                "--ledger",
                str(ledger),
            ]
        )
        == 0
    )
    processed = json.loads(capsys.readouterr().out)
    assert processed[0]["decision"] == "expense"

    search = tmp_path / "search.json"
    search.write_text(
        json.dumps({"message_ids": ["message-1", "message-2"], "next_page_token": "next"}),
        encoding="utf-8",
    )
    assert (
        cli.main(
            [
                "filter-new",
                str(search),
                "--database",
                str(database),
                "--ledger",
                str(ledger),
            ]
        )
        == 0
    )
    filtered = json.loads(capsys.readouterr().out)
    assert filtered == {
        "message_ids": ["message-2"],
        "already_processed_ids": ["message-1"],
    }
