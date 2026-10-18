from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mizu_kakeibo import cli
from mizu_kakeibo.constants import INBOX_MESSAGE_CONTRACT
from mizu_kakeibo.models import InboxMessage, MessageContent, MessageSource


@pytest.fixture(autouse=True)
def private_live_paths(tmp_path, monkeypatch) -> Path:
    """Point the CLI's default database and ledger into the test, never at the real ones."""
    live = tmp_path / "live"
    monkeypatch.setattr(cli, "LIVE_DATABASE", live / "kakeibo.sqlite3")
    monkeypatch.setattr(cli, "LIVE_LEDGER", live / "ledger.csv")
    return live


@pytest.fixture
def inbox_message() -> InboxMessage:
    return InboxMessage(
        contract_version=INBOX_MESSAGE_CONTRACT,
        source=MessageSource(provider="gmail", message_id="message-1", thread_id="thread-1"),
        received_at=datetime(2026, 10, 6, 1, 30, tzinfo=timezone.utc),
        sender="Example Store <receipts@example.invalid>",
        subject="Your receipt",
        content=MessageContent(
            text="Purchase completed at Example Store for MYR 42.50 on 2026-10-06.",
            source_type="text_plain",
            truncated=False,
        ),
    )


_HOLD_RUN_LOCK = """
import sys
from pathlib import Path
from mizu_kakeibo.runlock import run_lock
with run_lock(Path(sys.argv[1])):
    print("locked", flush=True)
    sys.stdin.read()
"""


@pytest.fixture
def hold_run_lock():
    """Start a separate process that holds a database's run lock until the test ends."""
    holders: list[subprocess.Popen[str]] = []

    def hold(database: Path) -> subprocess.Popen[str]:
        holder = subprocess.Popen(
            [sys.executable, "-c", _HOLD_RUN_LOCK, str(database)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        holders.append(holder)
        assert holder.stdout is not None
        assert holder.stdout.readline() == "locked\n"
        return holder

    yield hold
    for holder in holders:
        holder.kill()
        holder.wait()
