from __future__ import annotations

import fcntl
import os
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path

from .scheduled import MALAYSIA
from .storage import ReviewItem, Store

REVIEW_PAGE = "review.html"

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Mizu Kakeibo review</title>
<style>
body {{ font: 15px/1.5 -apple-system, sans-serif; margin: 2em auto; max-width: 48em; }}
li {{ margin-bottom: 1em; }}
.meta {{ color: #666; font-size: 13px; }}
</style>
</head>
<body>
<h1>Review list</h1>
{body}
</body>
</html>
"""


def gmail_url(message_id: str, thread_id: str | None) -> str | None:
    """Build the Gmail web link the connector reports, from hexadecimal Gmail IDs."""
    try:
        message = int(message_id, 16)
        thread = int(thread_id, 16) if thread_id else None
    except ValueError:
        return None
    if thread is None:
        return None
    return f"https://mail.google.com/mail/#all/thread-f:{thread}|msg-f:{message}"


def item_url(item: ReviewItem) -> str | None:
    return gmail_url(item.message_id, item.thread_id) if item.provider == "gmail" else None


def _added(created_at: str) -> str:
    try:
        return datetime.fromisoformat(created_at).astimezone(MALAYSIA).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return created_at


def review_page_path(database: Path) -> Path:
    return Path(database).parent / REVIEW_PAGE


def render_review_page(items: list[ReviewItem]) -> str:
    """Render the unresolved review items with their reasons, missing fields, and links only."""
    if not items:
        return _PAGE.format(body="<p>Nothing to review.</p>")
    entries = []
    for item in items:
        url = item_url(item)
        link = f'<a href="{escape(url)}">Open in Gmail</a>' if url is not None else "No Gmail link"
        missing = ", ".join(item.missing_fields) if item.missing_fields else "none"
        entries.append(
            f"<li><strong>{escape(item.reason)}</strong><br>\n"
            f"Missing fields: {escape(missing)}<br>\n"
            f"{link}<br>\n"
            f'<span class="meta">Added {escape(_added(item.created_at))} · {escape(item.provider)} '
            f"{escape(item.message_id)}</span></li>"
        )
    count = f"{len(items)} item{'' if len(items) == 1 else 's'} to review."
    return _PAGE.format(body=f"<p>{count}</p>\n<ol>\n" + "\n".join(entries) + "\n</ol>")


def write_review_page(store: Store) -> Path:
    """Rewrite the private review page beside the database from its unresolved review items.

    Writers wait for each other and read the items inside the wait, so the last write
    reflects every resolution committed before it. The page is replaced atomically.
    """
    path = review_page_path(store.database_path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path.with_name(f"{REVIEW_PAGE}.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        page = render_review_page(store.list_review_items())
        temporary, name = tempfile.mkstemp(dir=path.parent, prefix=f".{REVIEW_PAGE}.")
        try:
            with os.fdopen(temporary, "w", encoding="utf-8") as target:
                target.write(page)
            os.replace(name, path)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
    finally:
        os.close(descriptor)
    return path
