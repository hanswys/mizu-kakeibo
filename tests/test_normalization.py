from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from mizu_kakeibo.normalization import (
    BODY_HEAD_LENGTH,
    BODY_LIMIT,
    BODY_TAIL_LENGTH,
    OMISSION_MARKER,
    NormalizationError,
    gmail_raw_to_full_message,
    normalize_gmail_message,
)


def leaf(mime_type: str, content: str | None, **changes):
    value = {
        "part_id": "0",
        "mime_type": mime_type,
        "filename": "",
        "headers": [],
        "body": {
            "size": len(content or ""),
            "base64_url_content": None,
            "content": content,
            "attachment_id": None,
        },
        "read_attachment_supported": None,
        "parts": None,
    }
    value.update(changes)
    return value


def gmail(payload, **changes):
    value = {
        "id": "gmail-id",
        "thread_id": "thread-id",
        "internal_date": "1791250200000",
        "payload": payload,
    }
    value.update(changes)
    return value


def with_headers(payload, headers):
    payload["headers"] = [{"name": name, "value": value} for name, value in headers]
    return payload


def test_normalizes_plain_text_and_provider_mapping() -> None:
    payload = with_headers(
        leaf("text/plain", "Cafe\r\nPaid\u00a0MYR 4.20\x00\n\n\n\nThanks   "),
        [("fRoM", "Cafe <cafe@example.invalid>"), ("SUBJECT", "Receipt")],
    )
    result = normalize_gmail_message(gmail(payload))

    assert result.source.message_id == "gmail-id"
    assert result.received_at == datetime(2026, 10, 6, 1, 30, tzinfo=timezone.utc)
    assert result.sender == "Cafe <cafe@example.invalid>"
    assert result.content.text == "Cafe\nPaid MYR 4.20\n\n\nThanks"
    assert result.content.source_type == "text_plain"
    assert result.content.truncated is False


def test_multipart_alternative_prefers_plain_text() -> None:
    payload = {
        "mime_type": "multipart/alternative",
        "headers": [],
        "body": {"content": None},
        "parts": [
            leaf("text/html", "<p>HTML duplicate</p>"),
            leaf("text/plain", "Plain version"),
        ],
    }
    result = normalize_gmail_message(gmail(payload))
    assert result.content.text == "Plain version"
    assert result.content.source_type == "text_plain"


def test_converts_html_without_hidden_or_remote_content() -> None:
    html = """
        <style>.secret { display: block }</style>
        <script>steal()</script>
        <p>Paid &amp; complete</p>
        <div hidden>hidden text</div>
        <div aria-hidden="true">also hidden</div>
        <table><tr><td>MYR</td><td>12.00</td></tr></table>
        <img src="https://tracker.invalid/pixel">
    """
    result = normalize_gmail_message(gmail(leaf("text/html", html)))
    assert "Paid & complete" in result.content.text
    assert "MYR\t12.00" in result.content.text
    assert "steal" not in result.content.text
    assert "hidden text" not in result.content.text
    assert "tracker.invalid" not in result.content.text
    assert result.content.source_type == "html_converted"


def test_combines_non_alternative_text_parts_and_ignores_attachments() -> None:
    attached = leaf("text/plain", "private attachment", filename="details.txt")
    payload = {
        "mime_type": "multipart/mixed",
        "headers": [],
        "body": {"content": None},
        "parts": [leaf("text/plain", "First"), leaf("text/html", "<p>Second</p>"), attached],
    }
    result = normalize_gmail_message(gmail(payload))
    assert result.content.text == "First\n\nSecond"
    assert result.content.source_type == "mixed_text"
    assert "attachment" not in result.content.text


def test_does_not_decode_base64_url_content() -> None:
    payload = leaf("text/plain", None)
    payload["body"]["base64_url_content"] = "U2VjcmV0"
    result = normalize_gmail_message(gmail(payload))
    assert result.content.text == ""


@pytest.mark.parametrize(
    "delimiter",
    ["On Tue, 1 Oct 2026, Person wrote:", "-----Original Message-----"],
)
def test_removes_recognized_reply_history(delimiter: str) -> None:
    result = normalize_gmail_message(
        gmail(leaf("text/plain", f"Current message\n\n{delimiter}\nOld"))
    )
    assert result.content.text == "Current message"


def test_keeps_forwarded_message_and_delimiter_without_current_text() -> None:
    forwarded = "---------- Forwarded message ---------\nForwarded content"
    assert normalize_gmail_message(gmail(leaf("text/plain", forwarded))).content.text == forwarded
    only_old = "-----Original Message-----\nOld content"
    assert normalize_gmail_message(gmail(leaf("text/plain", only_old))).content.text == only_old


def test_truncates_body_with_head_tail_and_marker() -> None:
    body = "a" * BODY_HEAD_LENGTH + "x" * 100 + "z" * BODY_TAIL_LENGTH
    assert len(body) > BODY_LIMIT
    result = normalize_gmail_message(gmail(leaf("text/plain", body)))
    assert result.content.text == "a" * BODY_HEAD_LENGTH + OMISSION_MARKER + "z" * BODY_TAIL_LENGTH
    assert result.content.truncated is True


def test_truncates_sender_and_subject() -> None:
    payload = with_headers(
        leaf("text/plain", "Body"),
        [("From", "s" * 513), ("Subject", "x" * 1001)],
    )
    result = normalize_gmail_message(gmail(payload))
    assert len(result.sender) == 512
    assert len(result.subject) == 1000
    assert result.content.truncated is True


def test_falls_back_to_date_header() -> None:
    payload = with_headers(
        leaf("text/plain", "Body"),
        [("Date", "Tue, 06 Oct 2026 09:30:00 +0800")],
    )
    result = normalize_gmail_message(gmail(payload, internal_date="invalid"))
    assert result.received_at == datetime(2026, 10, 6, 1, 30, tzinfo=timezone.utc)


def test_invalid_dates_fail_instead_of_using_processing_time() -> None:
    with pytest.raises(NormalizationError):
        normalize_gmail_message(gmail(leaf("text/plain", "Body"), internal_date=None))


def connector_raw(rfc822: str, **changes):
    encoded = base64.urlsafe_b64encode(rfc822.encode("utf-8")).decode("ascii").rstrip("=")
    value = {
        "id": "gmail-id",
        "threadId": "thread-id",
        "internalDate": "1791250200000",
        "labelIds": ["INBOX"],
        "raw": encoded,
    }
    value.update(changes)
    return value


RAW_ALTERNATIVE = """\
From: =?UTF-8?Q?Caf=C3=A9_Store?= <receipts@example.invalid>
Subject: =?UTF-8?B?UmVjZWlwdCDinJM=?=
Date: Tue, 06 Oct 2026 09:30:00 +0800
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="outer"

--outer
Content-Type: multipart/alternative; boundary="inner"

--inner
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: quoted-printable

Paid MYR 4.20 at Caf=C3=A9 Store.=0A=
Thanks
--inner
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: quoted-printable

<p>Paid MYR 4.20 at Caf=C3=A9 Store.</p>
--inner--
--outer
Content-Type: text/plain; name="receipt.txt"
Content-Disposition: attachment; filename="receipt.txt"
Content-Transfer-Encoding: base64

QXR0YWNobWVudCB0ZXh0
--outer--
"""


def test_converts_connector_raw_message_into_mime_parts() -> None:
    full = gmail_raw_to_full_message(connector_raw(RAW_ALTERNATIVE))

    assert full["id"] == "gmail-id"
    assert full["thread_id"] == "thread-id"
    assert full["label_ids"] == ["INBOX"]
    assert full["internal_date"] == "1791250200000"
    payload = full["payload"]
    assert payload["mime_type"] == "multipart/mixed"
    alternative, attachment = payload["parts"]
    assert alternative["part_id"] == "0"
    plain, html = alternative["parts"]
    assert plain["part_id"] == "0.0"
    assert plain["body"]["content"] == "Paid MYR 4.20 at Café Store.\nThanks"
    assert html["mime_type"] == "text/html"
    assert attachment["filename"] == "receipt.txt"
    assert attachment["body"]["content"] is None


def test_normalizes_connector_raw_message() -> None:
    result = normalize_gmail_message(connector_raw(RAW_ALTERNATIVE))

    assert result.source.message_id == "gmail-id"
    assert result.source.thread_id == "thread-id"
    assert result.received_at == datetime(2026, 10, 6, 1, 30, tzinfo=timezone.utc)
    assert result.sender == "Café Store <receipts@example.invalid>"
    assert result.subject == "Receipt ✓"
    assert result.content.text == "Paid MYR 4.20 at Café Store.\nThanks"
    assert result.content.source_type == "text_plain"


def test_connector_raw_message_handles_8bit_html_and_numeric_date() -> None:
    rfc822 = (
        "From: Kedai <kedai@example.invalid>\n"
        "Subject: Résumé\n"
        "Content-Type: text/html; charset=utf-8\n"
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
        "<p>Jumlah RM 12.00</p><p>Terima kasih — Kedai</p>\n"
    )
    result = normalize_gmail_message(connector_raw(rfc822, internalDate=1791250200000))

    assert result.received_at == datetime(2026, 10, 6, 1, 30, tzinfo=timezone.utc)
    assert result.subject == "Résumé"
    assert result.content.text == "Jumlah RM 12.00\n\nTerima kasih — Kedai"
    assert result.content.source_type == "html_converted"


def test_connector_raw_message_falls_back_to_date_header() -> None:
    result = normalize_gmail_message(connector_raw(RAW_ALTERNATIVE, internalDate=None))
    assert result.received_at == datetime(2026, 10, 6, 1, 30, tzinfo=timezone.utc)


def test_invalid_connector_raw_message_fails() -> None:
    with pytest.raises(NormalizationError, match="base64url"):
        normalize_gmail_message(connector_raw("", raw="a"))
