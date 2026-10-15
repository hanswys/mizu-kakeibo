from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class Selection:
    message_ids: list[str]
    already_processed_ids: list[str]
    deferred_ids: list[str]
    excluded_ids: list[str]
    incomplete_thread_ids: list[str]

    def as_json(self) -> dict[str, list[str]]:
        return {
            "message_ids": self.message_ids,
            "already_processed_ids": self.already_processed_ids,
            "deferred_ids": self.deferred_ids,
            "excluded_ids": self.excluded_ids,
            "incomplete_thread_ids": self.incomplete_thread_ids,
        }


def _received_ms(message: dict[str, Any]) -> int | None:
    value = message.get("internalDate")
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def select_new_messages(
    metadata: dict[str, dict[str, Any]],
    incomplete_thread_ids: list[str],
    filter_new_ids: Callable[[list[str]], list[str]],
    *,
    received_since: datetime,
    limit: int,
) -> Selection:
    """Choose the oldest unprocessed inbox messages received since the sync start.

    A message is excluded when it lacks the INBOX label or a received time on or after the
    start. New messages beyond the limit are deferred to a later run.
    """
    if received_since.tzinfo is None:
        raise ValueError("the sync start must include a UTC offset")
    if limit < 1:
        raise ValueError("the batch limit must be at least 1")
    since_ms = int(received_since.timestamp() * 1000)
    eligible: list[tuple[int, str]] = []
    excluded: list[str] = []
    for message_id, message in metadata.items():
        received = _received_ms(message)
        labels = message.get("labelIds")
        if (
            received is None
            or received < since_ms
            or not isinstance(labels, list)
            or "INBOX" not in labels
        ):
            excluded.append(message_id)
        else:
            eligible.append((received, message_id))
    ordered = [message_id for _, message_id in sorted(eligible)]
    new_ids = filter_new_ids(ordered)
    new_id_set = set(new_ids)
    return Selection(
        message_ids=new_ids[:limit],
        already_processed_ids=[
            message_id for message_id in ordered if message_id not in new_id_set
        ],
        deferred_ids=new_ids[limit:],
        excluded_ids=sorted(excluded),
        incomplete_thread_ids=sorted(incomplete_thread_ids),
    )


def search_query(received_since: datetime, latest_processed: datetime | None) -> str:
    """Build the inbox search for a sync.

    The search starts at the newest processed message when it is later than the sync start,
    since earlier messages were processed oldest first. Gmail's date operators do not use the
    sync start's time zone, so the date starts one day early; selection applies the exact start.
    """
    if received_since.tzinfo is None:
        raise ValueError("the sync start must include a UTC offset")
    start = received_since
    if latest_processed is not None and latest_processed > start:
        start = latest_processed
    day = (start.astimezone(received_since.tzinfo) - timedelta(days=1)).date()
    return f"in:inbox after:{day:%Y/%m/%d}"
