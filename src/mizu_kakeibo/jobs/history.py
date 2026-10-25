"""An application's history (contract §9): `jobs-show N` for the Nth application in `jobs-list`.

It shows the application and every email linked to it, and opens a numbered one in Gmail. It
records nothing.
"""

from __future__ import annotations

from email.utils import parseaddr
from pathlib import Path

from ..review import SENDER_WIDTH, Console, _line, _shorten, gmail_url
from ..scheduled import MALAYSIA
from .storage import ApplicationHistory, LinkedEmail, read_history


class UnknownApplicationError(ValueError):
    """No application has the number `jobs-show` was given."""


def _sender(email: LinkedEmail) -> str:
    name, address = parseaddr(email.sender)
    return _line(name) or _line(address)


def _effect(email: LinkedEmail) -> str:
    return f"{email.effect.value} (reviewed)" if email.reviewed else email.effect.value


def render_history(history: ApplicationHistory, columns: int, numbered: bool = False) -> str:
    """The application, then each linked email's received date, sender, reported status, effect,
    and subject, oldest first; `numbered` numbers the emails to open.
    """
    application = history.application
    lines = [
        f"  Company    {_line(application.company)}",
        f"  Job title  {_line(application.job_title)}",
        f"  Status     {application.status.value}, updated {application.latest_update.isoformat()}",
    ]
    aliases = [f"{_line(company)} · {_line(job_title)}" for company, job_title in history.aliases]
    lines.append(f"  Aliases    {aliases[0] if aliases else 'none'}")
    lines.extend(f"             {alias}" for alias in aliases[1:])
    lines.append("")
    emails = history.emails
    count = len(emails)
    senders = [_shorten(_sender(email), SENDER_WIDTH) for email in emails]
    effects = [_effect(email) for email in emails]
    sender_width = max((len(sender) for sender in senders), default=0)
    effect_width = max((len(effect) for effect in effects), default=0)
    number_width = len(str(count))
    used = 2 + (number_width + 2 if numbered else 0) + 10 + 2 + sender_width + 2 + 9 + 2
    subject_width = max(columns - used - effect_width - 2, 10)
    for number, (email, sender, effect) in enumerate(zip(emails, senders, effects), start=1):
        received = email.received_at.astimezone(MALAYSIA).date()
        prefix = f"{number:>{number_width}}  " if numbered else ""
        subject = _shorten(_line(email.subject) or "(no subject)", subject_width)
        lines.append(
            f"  {prefix}{received:%a} {received.day:>2} {received:%b}  "
            f"{sender:<{sender_width}}  {email.status.value:<9}  "
            f"{effect:<{effect_width}}  {subject}"
        )
    lines.extend(["", f"  {count} email{'' if count == 1 else 's'}"])
    return "\n".join(lines)


def _url(email: LinkedEmail) -> str | None:
    if email.provider != "gmail":
        return None
    return gmail_url(email.message_id, email.thread_id)


def find_history(database_path: Path, number: str) -> ApplicationHistory:
    """Read the application `jobs-list` numbers `number`, or refuse a number it does not list."""
    value = number.strip()
    history = read_history(database_path, int(value)) if value.isdigit() else None
    if history is None:
        raise UnknownApplicationError(
            f"no application {value or '(none)'}; run mizu-kakeibo jobs-list for the numbers"
        )
    return history


def show(history: ApplicationHistory, console: Console) -> int:
    """Print an application's history; in a terminal, open picked emails in Gmail."""
    numbered = console.interactive()
    console.write(render_history(history, console.width(), numbered))
    if not numbered:
        return 0
    console.write()
    count = len(history.emails)
    try:
        while True:
            answer = console.ask("Number to open in Gmail, or Enter or q to quit: ").strip()
            if answer.lower() in ("", "q"):
                return 0
            if not (answer.isdigit() and 1 <= int(answer) <= count):
                console.write(f"  Type a number from 1 to {count}.")
                continue
            url = _url(history.emails[int(answer) - 1])
            if url is None:
                console.write(f"  Email {answer} has no Gmail link.")
            else:
                console.open(url)
    except (EOFError, KeyboardInterrupt):
        console.write()
    return 0
