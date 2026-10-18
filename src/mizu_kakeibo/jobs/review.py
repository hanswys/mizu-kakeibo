"""The job tracker's terminal review (contract §8) and application list.

It follows the expense app's `review`: a numbered list, then one item at a time with the first
lines of the email, its whole text, and its Gmail message a key away.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from email.utils import parseaddr
from typing import TypeVar

from ..models import InboxMessage
from ..review import _UNSAFE, PREVIEW_LINES, SENDER_WIDTH, WHY_WIDTH, Console, _line, _shorten
from ..review import gmail_url as _gmail_url
from ..scheduled import MALAYSIA
from .models import JobStatus
from .names import company_key, job_title_key
from .storage import (
    ApplicationRow,
    JobReviewError,
    JobReviewItem,
    JobStore,
    LikelyMatchError,
    ResolutionAction,
    ReviewItemResolvedError,
)
from .tracker import ReviewCause

FIELD_NAMES = {
    "company": "company",
    "job_title": "job title",
    "status": "status",
    "message_content": "text",
}
COMPANY_WIDTH = 30
T = TypeVar("T")


def item_url(item: JobReviewItem) -> str | None:
    return _gmail_url(item.message_id, item.thread_id) if item.provider == "gmail" else None


class _Quit(Exception):
    pass


@dataclass(frozen=True)
class _Entry:
    item: JobReviewItem
    message: InboxMessage

    @property
    def received(self) -> date:
        return self.message.received_at.astimezone(MALAYSIA).date()

    @property
    def sender_name(self) -> str:
        name, address = parseaddr(self.message.sender)
        return _line(name) or _line(address)


def _entries(store: JobStore) -> list[_Entry]:
    return [
        _Entry(item, store.message(item.provider, item.message_id))
        for item in store.list_review_items()
    ]


def _why(item: JobReviewItem) -> str:
    if item.cause is ReviewCause.LIKELY_MATCH:
        return "likely match"
    if item.cause is ReviewCause.CONTRADICTORY_OUTCOME:
        return "contradicts the outcome"
    if item.missing_fields:
        return "missing " + ", ".join(
            FIELD_NAMES.get(field, field) for field in item.missing_fields
        )
    return _line(item.reason)


def describe(application: ApplicationRow) -> str:
    return (
        f"#{application.id} {_line(application.company)} · {_line(application.job_title)} · "
        f"{application.status.value}, updated {application.latest_update.isoformat()}"
    )


def render_list(entries: Sequence[_Entry], columns: int) -> str:
    """Number each review item on one line: date, sender, subject, and why it waits."""
    count = len(entries)
    lines = [f"{count} message{'' if count == 1 else 's'} to review", ""]
    number_width = len(str(count))
    senders = [_shorten(entry.sender_name, SENDER_WIDTH) for entry in entries]
    whys = [_shorten(_why(entry.item), WHY_WIDTH) for entry in entries]
    sender_width = max(len(sender) for sender in senders)
    used = 2 + number_width + 2 + 10 + 2 + sender_width + 2 + 2 + max(len(why) for why in whys)
    subjects = [_line(entry.message.subject) for entry in entries]
    subject_width = min(max(len(subject) for subject in subjects), max(columns - used, 10))
    for number, (entry, sender, subject, why) in enumerate(
        zip(entries, senders, subjects, whys), start=1
    ):
        received = entry.received
        lines.append(
            f"  {number:>{number_width}}  {received:%a} {received.day:>2} {received:%b}  "
            f"{sender:<{sender_width}}  {_shorten(subject, subject_width):<{subject_width}}  {why}"
        )
    return "\n".join(lines)


def render_item(entry: _Entry) -> str:
    message, item = entry.message, entry.item
    received = message.received_at.astimezone(MALAYSIA)
    missing = ", ".join(FIELD_NAMES.get(field, field) for field in item.missing_fields)
    extracted = item.extracted
    values = [
        _line(value) if value else "?"
        for value in (
            extracted.company,
            extracted.job_title,
            extracted.status.value if extracted.status else None,
        )
    ]
    lines = [
        _line(message.subject) or "(no subject)",
        f"  From       {_line(message.sender)}",
        f"  Received   {received:%Y-%m-%d %H:%M}",
        f"  Why        {_why(item)}",
        f"  Reason     {_line(item.reason)}",
        f"  Missing    {missing or 'none'}",
        f"  Extracted  {' · '.join(values) if any(value != '?' for value in values) else 'none'}",
    ]
    if item.suggested is not None:
        lines.append(f"  Suggested  {describe(item.suggested)}")
    lines.append("")
    text = [line for line in message.content.text.splitlines() if line.strip()]
    lines.extend(f"    {_UNSAFE.sub('', line).rstrip()}" for line in text[:PREVIEW_LINES])
    if not text:
        lines.append("    (no text)")
    elif len(text) > PREVIEW_LINES:
        more = len(text) - PREVIEW_LINES
        lines.append(f"    … {more} more line{'' if more == 1 else 's'}, m shows the whole text")
    return "\n".join(lines)


def render_applications(applications: Sequence[ApplicationRow], columns: int) -> str:
    """One line per application: ID, latest update date, status, company, and job title."""
    if not applications:
        return "No applications yet."
    count = len(applications)
    numbers = [f"#{application.id}" for application in applications]
    number_width = max(len(number) for number in numbers)
    companies = [
        _shorten(_line(application.company), COMPANY_WIDTH) for application in applications
    ]
    company_width = max(len("Company"), *(len(company) for company in companies))
    used = 2 + number_width + 2 + 10 + 2 + 9 + 2 + company_width + 2
    title_width = max(columns - used, 10)
    lines = [
        f"{count} application{'' if count == 1 else 's'}",
        "",
        f"  {'':<{number_width}}  {'Updated':<10}  {'Status':<9}  "
        f"{'Company':<{company_width}}  Job title",
    ]
    for application, number, company in zip(applications, numbers, companies):
        lines.append(
            f"  {number:<{number_width}}  {application.latest_update.isoformat()}  "
            f"{application.status.value:<9}  {company:<{company_width}}  "
            f"{_shorten(_line(application.job_title), title_width)}"
        )
    return "\n".join(lines)


def application_list(store: JobStore, columns: int) -> str:
    """The `jobs-list` output: the applications, then what waits for review."""
    text = render_applications(store.list_applications(), columns)
    waiting = len(store.list_review_items())
    if waiting:
        text += (
            f"\n\n{waiting} message{'' if waiting == 1 else 's'} to review: "
            "run mizu-kakeibo jobs-review"
        )
    return text + "\n"


def parse_status(text: str) -> JobStatus:
    """Accept a status written whole or by its first letters."""
    value = text.strip().lower()
    matches = [status for status in JobStatus if value and status.value.startswith(value)]
    if len(matches) != 1:
        raise ValueError("Type applied, viewed, interview, rejected, or offered")
    return matches[0]


def _name_parser(label: str, key: Callable[[str], str]) -> Callable[[str], str]:
    def parse(text: str) -> str:
        value = text.strip()
        if not key(value):
            raise ValueError(f"The {label} needs a letter or digit")
        if len(value) > 200:
            raise ValueError(f"The {label} can have at most 200 characters")
        return value

    return parse


parse_company = _name_parser("company", company_key)
parse_job_title = _name_parser("job title", job_title_key)


@dataclass(frozen=True)
class _Values:
    company: str
    job_title: str
    status: JobStatus

    def __str__(self) -> str:
        return f"{_line(self.company)} · {_line(self.job_title)} · {self.status.value}"


class _Review:
    def __init__(self, store: JobStore, console: Console) -> None:
        self.store = store
        self.console = console

    def ask(self, prompt: str) -> str:
        answer = self.console.ask(prompt)
        if answer.strip().lower() == "q":
            raise _Quit
        return answer

    def ask_value(self, label: str, default: str | None, parse: Callable[[str], T]) -> T:
        prompt = f"{label} [{_line(default)}]: " if default else f"{label}: "
        while True:
            answer = self.ask(prompt)
            if not answer.strip() and default:
                answer = default
            try:
                return parse(answer)
            except ValueError as error:
                self.console.write(f"  {error}.")

    def ask_values(
        self, company: str | None, job_title: str | None, status: JobStatus | None
    ) -> _Values:
        self.console.write("Press Enter for the value in brackets.")
        return _Values(
            self.ask_value("Company", company, parse_company),
            self.ask_value("Job title", job_title, parse_job_title),
            self.ask_value("Status", status.value if status else None, parse_status),
        )

    def confirm(self, decision: str) -> bool:
        self.console.write(decision)
        while True:
            answer = self.ask("Save? y/n: ").strip().lower()
            if answer == "y":
                return True
            if answer == "n":
                self.console.write("Not saved.")
                return False

    def run(self) -> None:
        while True:
            entries = _entries(self.store)
            if not entries:
                self.console.write("Nothing to review.")
                return
            self.console.write(render_list(entries, self.console.width()))
            self.console.write()
            start = self.pick(len(entries))
            for number, entry in enumerate(entries[start:], start=start + 1):
                self.console.write()
                if not self.unresolved(entry):
                    self.console.write(f"Message {number} was already resolved; passing over it.")
                    continue
                self.resolve(entry)
            self.console.write()

    def pick(self, count: int) -> int:
        while True:
            answer = self.ask("Number to review, or q to quit: ").strip()
            if answer.isdigit() and 1 <= int(answer) <= count:
                return int(answer) - 1
            self.console.write(f"  Type a number from 1 to {count}.")

    def unresolved(self, entry: _Entry) -> bool:
        return any(item.id == entry.item.id for item in self.store.list_review_items())

    def keys(self, entry: _Entry) -> str:
        keys = ["a accept"] if self.acceptable(entry) else []
        keys += [
            "e edit",
            "t attach to another",
            "c new application",
            "n not an application",
            "m whole text",
            "g Gmail",
            "s skip",
            "q quit",
        ]
        return " · ".join(keys) + ": "

    @staticmethod
    def acceptable(entry: _Entry) -> bool:
        return entry.item.suggested is not None and entry.item.extracted.status is not None

    def resolve(self, entry: _Entry) -> None:
        """Show one item and act on its keys until it is resolved or skipped."""
        self.console.write(render_item(entry))
        self.console.write()
        actions = {
            "e": self.edit,
            "t": self.attach_another,
            "c": self.create,
            "n": self.not_application,
        }
        if self.acceptable(entry):
            actions["a"] = self.accept
        while True:
            key = self.ask(self.keys(entry)).strip().lower()
            if key in actions:
                if actions[key](entry):
                    return
            elif key == "m":
                lines = entry.message.content.text.splitlines()
                self.console.page("".join(f"{_UNSAFE.sub('', line)}\n" for line in lines))
            elif key == "g":
                url = item_url(entry.item)
                if url is None:
                    self.console.write("  This message has no Gmail link.")
                else:
                    self.console.open(url)
            elif key == "s":
                return
            else:
                self.console.write(f"  Press {', '.join(actions)}, m, g, s, or q.")

    def accept(self, entry: _Entry) -> bool:
        suggested, extracted = entry.item.suggested, entry.item.extracted
        assert suggested is not None and extracted.status is not None
        company, job_title = extracted.company, extracted.job_title
        named = company is not None and job_title is not None
        decision = f"Attach to {describe(suggested)} as {extracted.status.value}"
        if company is not None and job_title is not None:
            decision += self.alias_note(company, job_title)
        if not self.confirm(decision):
            return False
        return self.save(
            entry,
            ResolutionAction.ATTACH,
            application_id=suggested.id,
            company=company if named else None,
            job_title=job_title if named else None,
            status=extracted.status,
        )

    def alias_note(self, company: str, job_title: str) -> str:
        """Say when attaching under these names saves them as another name of the application."""
        if self.store.match(company, job_title)[0] == "exact":
            return ""
        return f"\n  {_line(company)} · {_line(job_title)} becomes another name for it"

    def edit(self, entry: _Entry) -> bool:
        extracted = entry.item.extracted
        values = self.ask_values(extracted.company, extracted.job_title, extracted.status)
        kind, application = self.store.match(values.company, values.job_title)
        if kind == "likely":
            assert application is not None
            self.console.write(
                f"  This looks like application {describe(application)}. "
                "Press t to attach it there, or c to make a new application."
            )
            return False
        if kind == "exact":
            assert application is not None
            decision = f"Attach to {describe(application)} as {values.status.value}"
        else:
            decision = f"New application  {values}"
        if not self.confirm(decision):
            return False
        return self.save(
            entry,
            ResolutionAction.MATCH,
            company=values.company,
            job_title=values.job_title,
            status=values.status,
        )

    def attach_another(self, entry: _Entry) -> bool:
        applications = {
            application.id: application for application in self.store.list_applications()
        }
        if not applications:
            self.console.write("  There are no applications yet; press c to make one.")
            return False
        self.console.write(render_applications(list(applications.values()), self.console.width()))
        self.console.write()

        def parse_number(text: str) -> ApplicationRow:
            number = text.strip().removeprefix("#")
            if not number.isdigit() or int(number) not in applications:
                raise ValueError("Type the number of an application in the list")
            return applications[int(number)]

        application = self.ask_value("Application number", None, parse_number)
        extracted = entry.item.extracted
        values = self.ask_values(
            extracted.company or application.company,
            extracted.job_title or application.job_title,
            extracted.status,
        )
        kind, owner = self.store.match(values.company, values.job_title)
        if kind == "exact" and owner is not None and owner.id != application.id:
            self.console.write(
                f"  Those names belong to application {describe(owner)}; attach it there instead."
            )
            return False
        decision = f"Attach to {describe(application)} as {values.status.value}"
        decision += self.alias_note(values.company, values.job_title)
        if not self.confirm(decision):
            return False
        return self.save(
            entry,
            ResolutionAction.ATTACH,
            application_id=application.id,
            company=values.company,
            job_title=values.job_title,
            status=values.status,
        )

    def create(self, entry: _Entry) -> bool:
        extracted = entry.item.extracted
        values = self.ask_values(extracted.company, extracted.job_title, extracted.status)
        kind, owner = self.store.match(values.company, values.job_title)
        if kind == "exact" and owner is not None:
            self.console.write(
                f"  That is already application {describe(owner)}; press t to attach to it."
            )
            return False
        if not self.confirm(f"New application  {values}"):
            return False
        return self.save(
            entry,
            ResolutionAction.CREATE,
            company=values.company,
            job_title=values.job_title,
            status=values.status,
        )

    def not_application(self, entry: _Entry) -> bool:
        if not self.confirm("Not an application"):
            return False
        return self.save(entry, ResolutionAction.NOT_APPLICATION)

    def save(self, entry: _Entry, action: ResolutionAction, **values: object) -> bool:
        """Save a resolution; True when the item is done, False to ask for another key."""
        try:
            self.store.resolve_review(entry.item.id, action, **values)  # type: ignore[arg-type]
        except ReviewItemResolvedError:
            self.console.write("This message was already resolved elsewhere; nothing was saved.")
            return True
        except LikelyMatchError as error:
            self.console.write(
                f"  This looks like application {describe(error.application)}; nothing was saved. "
                "Press t to attach it there, or c to make a new application."
            )
            return False
        except JobReviewError as error:
            self.console.write(f"  Not saved: {error}.")
            return False
        self.console.write("Saved.")
        return True


def review(store: JobStore, console: Console) -> int:
    """Resolve the unresolved job review items one at a time."""
    if not console.interactive():
        entries = _entries(store)
        console.write(render_list(entries, console.width()) if entries else "Nothing to review.")
        return 0
    try:
        _Review(store, console).run()
    except _Quit:
        pass
    except (EOFError, KeyboardInterrupt):
        console.write()
    return 0
