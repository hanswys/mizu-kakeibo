"""The skim list (contract §9) for a period, worked out from the stored decisions.

`jobs-skim` lists the job-related `not_application` emails received in a period, so the user can
skim them for a missed status update, and opens a numbered one in Gmail. It records nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from email.utils import parseaddr
from pathlib import Path

from ..review import SENDER_WIDTH, Console, _line, _shorten, gmail_url
from ..scheduled import MALAYSIA
from ..spending import Period
from .processor import looks_job_related
from .storage import NotApplication, read_not_applications


def _received(message: NotApplication) -> date:
    return message.received_at.astimezone(MALAYSIA).date()


def skim_list(database_path: Path, start: date, end: date) -> list[NotApplication]:
    """The skim list of the emails received from `start` to `end` in Malaysia time, oldest first."""
    return [
        message
        for message in read_not_applications(database_path)
        if start <= _received(message) <= end and looks_job_related(message.sender, message.subject)
    ]


def _sender(message: NotApplication) -> str:
    name, address = parseaddr(message.sender)
    return _line(name) or _line(address)


def render_skim(
    period: Period, messages: Sequence[NotApplication], columns: int, numbered: bool = False
) -> str:
    """List each email's received date, sender, and subject; `numbered` numbers them to open."""
    lines = [period.label, ""]
    if not messages:
        lines.append("  Nothing to skim.")
        return "\n".join(lines)
    count = len(messages)
    senders = [_shorten(_sender(message), SENDER_WIDTH) for message in messages]
    sender_width = max(len(sender) for sender in senders)
    number_width = len(str(count))
    used = 2 + (number_width + 2 if numbered else 0) + 10 + 2 + sender_width + 2
    subject_width = max(columns - used, 10)
    for number, (message, sender) in enumerate(zip(messages, senders), start=1):
        received = _received(message)
        prefix = f"{number:>{number_width}}  " if numbered else ""
        subject = _shorten(_line(message.subject) or "(no subject)", subject_width)
        lines.append(
            f"  {prefix}{received:%a} {received.day:>2} {received:%b}  "
            f"{sender:<{sender_width}}  {subject}"
        )
    lines.extend(["", f"  {count} email{'' if count == 1 else 's'} to skim"])
    return "\n".join(lines)


def _url(message: NotApplication) -> str | None:
    if message.provider != "gmail":
        return None
    return gmail_url(message.message_id, message.thread_id)


def skim(database_path: Path, period: Period, console: Console) -> int:
    """Print the period's skim list; in a terminal, open picked emails in Gmail."""
    messages = skim_list(database_path, period.start, period.end)
    numbered = console.interactive() and bool(messages)
    console.write(render_skim(period, messages, console.width(), numbered))
    if not numbered:
        return 0
    console.write()
    count = len(messages)
    try:
        while True:
            answer = console.ask("Number to open in Gmail, or Enter or q to quit: ").strip()
            if answer.lower() in ("", "q"):
                return 0
            if not (answer.isdigit() and 1 <= int(answer) <= count):
                console.write(f"  Type a number from 1 to {count}.")
                continue
            url = _url(messages[int(answer) - 1])
            if url is None:
                console.write(f"  Email {answer} has no Gmail link.")
            else:
                console.open(url)
    except (EOFError, KeyboardInterrupt):
        console.write()
    return 0
