from __future__ import annotations

import base64
import binascii
import email
import json
import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

from .constants import INBOX_MESSAGE_CONTRACT
from .models import InboxMessage, MessageContent, MessageSource

BODY_HEAD_LENGTH = 18_000
BODY_TAIL_LENGTH = 6_000
BODY_LIMIT = BODY_HEAD_LENGTH + BODY_TAIL_LENGTH
OMISSION_MARKER = "\n\n[... content omitted by Mizu Kakeibo ...]\n\n"


class NormalizationError(ValueError):
    """A Gmail message cannot be converted into a valid contract envelope."""


class _ReadableHTMLParser(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "caption",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }
    _CELL_TAGS = {"td", "th"}
    _ALWAYS_HIDDEN = {"script", "style", "template", "head", "noscript"}
    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {name.lower(): (value or "") for name, value in attrs}
        style = re.sub(r"\s+", "", attrs_map.get("style", "").lower())
        is_hidden = (
            tag in self._ALWAYS_HIDDEN
            or "hidden" in attrs_map
            or attrs_map.get("aria-hidden", "").lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        if self._hidden_depth or is_hidden:
            if tag not in self._VOID_TAGS:
                self._hidden_depth += 1
            return
        if tag in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br" and not self._hidden_depth:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._hidden_depth:
            self._hidden_depth -= 1
            return
        if tag in self._BLOCK_TAGS:
            self._chunks.append("\n")
        elif tag in self._CELL_TAGS:
            self._chunks.append("\t")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def html_to_text(value: str) -> str:
    parser = _ReadableHTMLParser()
    parser.feed(value)
    parser.close()
    return parser.text()


def _header(headers: Sequence[dict[str, Any]], name: str) -> str:
    wanted = name.casefold()
    for header in headers:
        if str(header.get("name", "")).casefold() == wanted:
            value = header.get("value")
            return value if isinstance(value, str) else ""
    return ""


def _received_at(message: dict[str, Any], headers: Sequence[dict[str, Any]]) -> datetime:
    internal_date = message.get("internal_date")
    try:
        if isinstance(internal_date, str) and internal_date.strip():
            milliseconds = int(internal_date)
            if milliseconds < 0:
                raise ValueError
            return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        pass

    date_header = _header(headers, "Date")
    try:
        parsed = parsedate_to_datetime(date_header)
        if parsed is not None and parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError):
        pass
    raise NormalizationError("message has no valid internal_date or Date header")


def _leaf_text(part: dict[str, Any]) -> tuple[str, str] | None:
    mime_type = str(part.get("mime_type", "")).split(";", 1)[0].strip().lower()
    if mime_type not in {"text/plain", "text/html"}:
        return None
    body = part.get("body")
    if not isinstance(body, dict) or not isinstance(body.get("content"), str):
        return None
    if part.get("filename") or body.get("attachment_id"):
        return None
    content = body["content"]
    if not content.strip():
        return None
    if mime_type == "text/html":
        converted = html_to_text(content)
        return (converted, "html_converted") if converted.strip() else None
    return content, "text_plain"


def _extract_text(part: dict[str, Any]) -> list[tuple[str, str]]:
    mime_type = str(part.get("mime_type", "")).split(";", 1)[0].strip().lower()
    children = part.get("parts")
    if mime_type == "multipart/alternative" and isinstance(children, list):
        choices = [_extract_text(child) for child in children if isinstance(child, dict)]
        usable = [choice for choice in choices if choice]
        if not usable:
            return []
        for choice in usable:
            if all(kind == "text_plain" for _, kind in choice):
                return choice
        return usable[0]
    if mime_type.startswith("multipart/") and isinstance(children, list):
        result: list[tuple[str, str]] = []
        for child in children:
            if isinstance(child, dict):
                result.extend(_extract_text(child))
        return result
    leaf = _leaf_text(part)
    return [leaf] if leaf else []


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    value = "".join(
        character
        for character in value
        if character in {"\n", "\t"} or unicodedata.category(character) != "Cc"
    )
    lines = [line.rstrip() for line in value.split("\n")]
    value = "\n".join(lines).strip()
    return re.sub(r"\n{4,}", "\n\n\n", value)


_REPLY_DELIMITERS = (
    re.compile(r"(?im)^On [^\n]{1,500} wrote:\s*$"),
    re.compile(r"(?im)^-{5,}\s*Original Message\s*-{5,}\s*$"),
)


def _remove_quoted_reply(value: str) -> str:
    matches = [match for pattern in _REPLY_DELIMITERS if (match := pattern.search(value))]
    if not matches:
        return value
    first = min(matches, key=lambda match: match.start())
    current = value[: first.start()].strip()
    return current if current else value


def _shorten(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _mime_headers(part: EmailMessage) -> list[dict[str, str]]:
    headers = []
    for name, raw_value in part.raw_items():
        try:
            value = str(part.policy.header_fetch_parse(name, raw_value))
        except (IndexError, TypeError, ValueError):
            value = re.sub(r"\r?\n[ \t]+", " ", str(raw_value)).strip()
        headers.append({"name": name, "value": value})
    return headers


def _mime_part(part: EmailMessage, part_id: str) -> dict[str, Any]:
    mime_type = part.get_content_type()
    filename = part.get_filename() or ""
    children = None
    content = None
    size = 0
    if part.get_content_maintype() == "multipart":
        children = [
            _mime_part(child, f"{part_id}.{index}" if part_id else str(index))
            for index, child in enumerate(part.iter_parts())
        ]
    elif not part.is_multipart():
        payload = part.get_payload(decode=True)
        size = len(payload) if isinstance(payload, bytes) else 0
        is_text = mime_type in {"text/plain", "text/html"}
        if is_text and isinstance(payload, bytes) and not filename and not part.is_attachment():
            charset = part.get_content_charset() or "utf-8"
            try:
                content = payload.decode(charset, errors="replace")
            except LookupError:
                content = payload.decode("utf-8", errors="replace")
    return {
        "part_id": part_id,
        "mime_type": mime_type,
        "filename": filename,
        "headers": _mime_headers(part),
        "body": {
            "size": size,
            "base64_url_content": None,
            "content": content,
            "attachment_id": None,
        },
        "parts": children,
    }


def gmail_raw_to_full_message(message: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a Gmail connector `RAW` response as the full-message shape the normalizer reads."""
    raw = message.get("raw")
    if not isinstance(raw, str) or not raw:
        raise NormalizationError("Gmail raw message has no raw content")
    try:
        data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError) as error:
        raise NormalizationError("Gmail raw message is not valid base64url") from error
    parsed = email.message_from_bytes(data, policy=policy.default)
    internal_date = message.get("internalDate", message.get("internal_date"))
    if isinstance(internal_date, int) and not isinstance(internal_date, bool):
        internal_date = str(internal_date)
    return {
        "id": message.get("id"),
        "thread_id": message.get("threadId", message.get("thread_id")),
        "label_ids": message.get("labelIds", message.get("label_ids")),
        "internal_date": internal_date if isinstance(internal_date, str) else None,
        "payload": _mime_part(parsed, ""),
    }


def normalize_gmail_message(message: dict[str, Any]) -> InboxMessage:
    """Convert one Gmail full-message or connector `RAW` response into the v0 envelope."""
    if not isinstance(message, dict):
        raise NormalizationError("Gmail message must be a JSON object")
    if message.get("payload") is None and isinstance(message.get("raw"), str):
        message = gmail_raw_to_full_message(message)
    message_id = message.get("id")
    if not isinstance(message_id, str) or not message_id:
        raise NormalizationError("Gmail message has no id")
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise NormalizationError("Gmail message has no payload")
    headers = payload.get("headers")
    if not isinstance(headers, list):
        headers = []
    valid_headers = [header for header in headers if isinstance(header, dict)]

    fragments = _extract_text(payload)
    normalized_fragments = []
    for fragment, kind in fragments:
        normalized = _normalize_text(fragment)
        if normalized:
            normalized_fragments.append((normalized, kind))
    if not normalized_fragments:
        text = ""
        source_type = "text_plain"
    else:
        text = "\n\n".join(fragment for fragment, _ in normalized_fragments)
        source_type = normalized_fragments[0][1] if len(normalized_fragments) == 1 else "mixed_text"
    text = _remove_quoted_reply(_normalize_text(text))

    sender, sender_truncated = _shorten(_normalize_text(_header(valid_headers, "From")), 512)
    subject, subject_truncated = _shorten(_normalize_text(_header(valid_headers, "Subject")), 1000)
    body_truncated = len(text) > BODY_LIMIT
    if body_truncated:
        text = text[:BODY_HEAD_LENGTH] + OMISSION_MARKER + text[-BODY_TAIL_LENGTH:]

    thread_id = message.get("thread_id")
    if thread_id is not None and not isinstance(thread_id, str):
        raise NormalizationError("Gmail thread_id must be a string or null")
    return InboxMessage(
        contract_version=INBOX_MESSAGE_CONTRACT,
        source=MessageSource(provider="gmail", message_id=message_id, thread_id=thread_id),
        received_at=_received_at(message, valid_headers),
        sender=sender,
        subject=subject,
        content=MessageContent(
            text=text,
            source_type=source_type,
            truncated=sender_truncated or subject_truncated or body_truncated,
        ),
    )


def parse_inbox_message(value: Any) -> InboxMessage:
    """Accept a normalized v0 envelope or a Gmail full-message or connector `RAW` response."""
    if isinstance(value, dict) and value.get("contract_version") == INBOX_MESSAGE_CONTRACT:
        return InboxMessage.model_validate_json(json.dumps(value))
    return normalize_gmail_message(value)
