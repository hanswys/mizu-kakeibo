from __future__ import annotations

import base64
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
    assert processed["results"][0]["decision"] == "expense"
    assert processed["summary"]["expense"] == ["message-1"]

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


def raw_connector_message(message_id: str, internal_date: str) -> dict:
    rfc822 = (
        "From: Example Store <receipts@example.invalid>\r\n"
        "Subject: Receipt\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "Purchase completed for MYR 42.50 on 2026-10-06.\r\n"
    )
    return {
        "id": message_id,
        "threadId": message_id,
        "internalDate": internal_date,
        "labelIds": ["INBOX"],
        "raw": base64.urlsafe_b64encode(rfc822.encode()).decode().rstrip("="),
    }


def connector_session(tmp_path, calls) -> str:
    lines = []
    for index, (tool, result) in enumerate(calls):
        lines.append(
            {
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"toolu_{index}",
                            "name": f"mcp__claude_ai_Gmail__{tool}",
                        }
                    ]
                }
            }
        )
        lines.append(
            {
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"toolu_{index}",
                            "content": json.dumps(result),
                        }
                    ]
                }
            }
        )
    session = tmp_path / "session.jsonl"
    session.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    return str(session)


def test_connector_sync_selects_collects_processes_and_links_review_items(
    tmp_path, monkeypatch, capsys
) -> None:
    received = "1791302400000"  # 2026-10-07T00:00:00+08:00
    old = "1790000000000"  # 2026-09-23, before the sync start
    first = raw_connector_message("1a13f73d4246d612", received)
    second = raw_connector_message("1a13f724be7b45ad", str(int(received) + 1))
    session = connector_session(
        tmp_path,
        [
            (
                "search_threads",
                {
                    "threads": [
                        {
                            "id": message["id"],
                            "messageCount": 1,
                            "messages": [
                                {
                                    "id": message["id"],
                                    "internalDate": message["internalDate"],
                                    "labelIds": ["INBOX"],
                                }
                            ],
                        }
                        for message in (first, second)
                    ]
                    + [
                        {
                            "id": "old-thread",
                            "messageCount": 1,
                            "messages": [{"id": "old", "internalDate": old, "labelIds": ["INBOX"]}],
                        }
                    ]
                },
            ),
            ("get_message", first),
            ("get_message", second),
        ],
    )
    storage = ["--database", str(tmp_path / "db.sqlite3"), "--ledger", str(tmp_path / "l.csv")]
    selection = tmp_path / "selection.json"
    messages = tmp_path / "messages.json"
    monkeypatch.setattr(cli, "OllamaAnalyser", FakeOllamaAnalyser)
    review = ExpenseAnalysisOutput(
        contract_version=ANALYSIS_OUTPUT_CONTRACT,
        decision=AnalysisDecision.NEEDS_REVIEW,
        reason="Required transaction details are incomplete",
        transaction=None,
        missing_fields=[],
    )
    monkeypatch.setattr(FakeOllamaAnalyser, "analyse", lambda self, _: review)

    assert (
        cli.main(
            ["select-new", "--session", session, "--limit", "1", "--output", str(selection)]
            + storage
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["message_ids"] == 1
    assert json.loads(selection.read_text()) == {
        "message_ids": [first["id"]],
        "already_processed_ids": [],
        "deferred_ids": [second["id"]],
        "excluded_ids": ["old"],
        "incomplete_thread_ids": [],
    }

    assert (
        cli.main(["collect-raw", str(selection), "--session", session, "--output", str(messages)])
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"collected": [first["id"]], "missing": []}

    assert cli.main(["process", str(messages), "--selection", str(selection)] + storage) == 0
    summary = json.loads(capsys.readouterr().out)["summary"]
    assert summary == {
        "expense": [],
        "not_expense": [],
        "needs_review": [first["id"]],
        "skipped": [],
        "failed": [],
        "deferred": [second["id"]],
    }

    assert cli.main(["review-list"] + storage) == 0
    [item] = json.loads(capsys.readouterr().out)
    assert item["gmail_url"] == (
        "https://mail.google.com/mail/#all/thread-f:1879117312020633106|msg-f:1879117312020633106"
    )

    assert cli.main(["select-new", "--session", session] + storage) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["message_ids"] == [second["id"]]
    assert replay["already_processed_ids"] == [first["id"]]


def test_collect_raw_fails_when_a_selected_message_was_not_fetched(tmp_path, capsys) -> None:
    session = connector_session(tmp_path, [])
    ids = tmp_path / "ids.json"
    ids.write_text(json.dumps(["missing-id"]))

    assert (
        cli.main(
            [
                "collect-raw",
                str(ids),
                "--session",
                session,
                "--output",
                str(tmp_path / "messages.json"),
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out) == {"collected": [], "missing": ["missing-id"]}
