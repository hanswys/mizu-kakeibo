from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mizu_kakeibo.constants import INBOX_MESSAGE_CONTRACT
from mizu_kakeibo.models import InboxMessage, MessageContent, MessageSource


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
