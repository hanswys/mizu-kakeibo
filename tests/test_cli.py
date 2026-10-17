from __future__ import annotations

import base64
import json
from datetime import date

import pytest

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
            runtime_version="0.40.5",
            model="qwen3.5:4b-q4_K_M",
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
    received = "1792166400000"  # 2026-10-17T00:00:00+08:00
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
                        },
                        {
                            "id": "alert-thread",
                            "messageCount": 1,
                            "messages": [
                                {
                                    "id": "alert",
                                    "internalDate": received,
                                    "labelIds": ["INBOX", "CATEGORY_UPDATES"],
                                    "sender": "jobalerts-noreply@linkedin.com",
                                }
                            ],
                        },
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
        "filtered_ids": ["alert"],
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
        "filtered": ["alert"],
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

    assert cli.main(["search-query"] + storage) == 0
    assert json.loads(capsys.readouterr().out) == {
        "query": "in:inbox -category:promotions -category:social after:2026/10/16"
    }
    assert cli.main(["search-query", "--full"] + storage) == 0
    assert json.loads(capsys.readouterr().out) == {
        "query": "in:inbox -category:promotions -category:social after:2026/10/15"
    }


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


def test_ledger_correct_rewrites_the_ledger_row_once(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "gmail.json"
    source.write_text(json.dumps(gmail_message()), encoding="utf-8")
    ledger = tmp_path / "ledger.csv"
    storage = ["--database", str(tmp_path / "private.sqlite3"), "--ledger", str(ledger)]
    monkeypatch.setattr(cli, "OllamaAnalyser", FakeOllamaAnalyser)
    assert cli.main(["process", str(source)] + storage) == 0
    capsys.readouterr()

    correct = ["ledger-correct", "gmail", "message-1", "--merchant", "Example Delivery"]
    correct += ["--transaction-date", "2026-10-05"]
    previous = {
        "merchant": "Example Store",
        "amount_minor": 4250,
        "currency": "MYR",
        "transaction_date": "2026-10-06",
    }
    corrected = {**previous, "merchant": "Example Delivery", "transaction_date": "2026-10-05"}
    assert cli.main(correct + storage) == 0
    assert json.loads(capsys.readouterr().out) == {
        "provider": "gmail",
        "message_id": "message-1",
        "status": "corrected",
        "previous": previous,
        "transaction": corrected,
    }
    assert cli.main(correct + storage) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "unchanged"
    assert ledger.read_text(encoding="utf-8").splitlines() == [
        "source_provider,gmail_message_id,merchant,amount_minor,currency,transaction_date",
        "gmail,message-1,Example Delivery,4250,MYR,2026-10-05",
    ]

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["ledger-correct", "gmail", "message-2", "--merchant", "Example"] + storage)
    assert exit_info.value.code == 2
    assert "no logged expense" in capsys.readouterr().err


@pytest.mark.parametrize("reevaluate", [[], ["--reevaluate"]])
def test_process_records_nothing_while_another_run_holds_the_lock(
    tmp_path, monkeypatch, capsys, hold_run_lock, reevaluate
) -> None:
    source = tmp_path / "gmail.json"
    source.write_text(json.dumps(gmail_message()), encoding="utf-8")
    database = tmp_path / "private.sqlite3"
    ledger = tmp_path / "ledger.csv"
    monkeypatch.setattr(cli, "OllamaAnalyser", FakeOllamaAnalyser)
    holder = hold_run_lock(database)

    process = ["process", str(source), "--database", str(database), "--ledger", str(ledger)]
    assert cli.main(process + reevaluate) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "locked"
    assert report["holder"]["pid"] == holder.pid
    assert not database.exists()
    assert not ledger.exists()


def test_review_and_correction_work_while_a_sync_holds_the_lock(
    tmp_path, monkeypatch, capsys, hold_run_lock
) -> None:
    source = tmp_path / "gmail.json"
    message = gmail_message()
    review = {**message, "id": "message-2"}
    source.write_text(json.dumps([message, review]), encoding="utf-8")
    database = tmp_path / "private.sqlite3"
    storage = ["--database", str(database), "--ledger", str(tmp_path / "ledger.csv")]

    class ReviewSecond(FakeOllamaAnalyser):
        calls = 0

        def analyse(self, analyser_input):
            ReviewSecond.calls += 1
            if ReviewSecond.calls == 1:
                return self.output
            return ExpenseAnalysisOutput(
                contract_version=ANALYSIS_OUTPUT_CONTRACT,
                decision=AnalysisDecision.NEEDS_REVIEW,
                reason="Fictional policy review",
                transaction=None,
                missing_fields=[],
            )

    monkeypatch.setattr(cli, "OllamaAnalyser", ReviewSecond)
    assert cli.main(["process", str(source)] + storage) == 0
    capsys.readouterr()
    hold_run_lock(database)

    assert cli.main(["review-resolve", "gmail", "message-2", "not_expense"] + storage) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "resolved"
    correct = ["ledger-correct", "gmail", "message-1", "--merchant", "Example Delivery"]
    assert cli.main(correct + storage) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "corrected"
