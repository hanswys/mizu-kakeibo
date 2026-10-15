from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mizu_kakeibo.selection import search_query, select_new_messages

SINCE = datetime.fromisoformat("2026-10-01T00:00:00+08:00")
SINCE_MS = int(SINCE.timestamp() * 1000)


def message(received_ms: int, labels=("INBOX",)) -> dict:
    return {"internalDate": str(received_ms), "labelIds": list(labels)}


def test_selects_oldest_new_inbox_messages_up_to_the_limit() -> None:
    metadata = {
        "late": message(SINCE_MS + 30),
        "early": message(SINCE_MS),
        "middle": message(SINCE_MS + 20),
        "done": message(SINCE_MS + 10),
        "before": message(SINCE_MS - 1),
        "sent": message(SINCE_MS + 5, labels=("SENT",)),
        "undated": {"labelIds": ["INBOX"]},
    }

    selection = select_new_messages(
        metadata,
        ["t2"],
        lambda ids: [message_id for message_id in ids if message_id != "done"],
        received_since=SINCE,
        limit=2,
    )

    assert selection.as_json() == {
        "message_ids": ["early", "middle"],
        "already_processed_ids": ["done"],
        "deferred_ids": ["late"],
        "excluded_ids": ["before", "sent", "undated"],
        "incomplete_thread_ids": ["t2"],
    }


def test_rejects_a_start_without_offset_or_an_empty_batch() -> None:
    with pytest.raises(ValueError):
        select_new_messages({}, [], list, received_since=datetime(2026, 10, 1), limit=1)
    with pytest.raises(ValueError):
        select_new_messages({}, [], list, received_since=SINCE, limit=0)


def test_search_starts_a_day_before_the_later_of_start_and_progress() -> None:
    utc = timezone.utc
    assert search_query(SINCE, None) == "in:inbox after:2026/09/30"
    assert search_query(SINCE, datetime(2026, 9, 1, tzinfo=utc)) == "in:inbox after:2026/09/30"
    # 2026-10-05T20:00Z is 2026-10-06 in Malaysia time.
    progress = datetime(2026, 10, 5, 20, tzinfo=utc)
    assert search_query(SINCE, progress) == "in:inbox after:2026/10/05"
    assert search_query(SINCE, progress + timedelta(hours=-8)) == "in:inbox after:2026/10/04"
    with pytest.raises(ValueError):
        search_query(datetime(2026, 10, 1), None)
