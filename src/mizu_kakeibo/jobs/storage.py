"""The job tracker's private SQLite database (contract §10), separate from the expense store.

Matching and status changes run through `tracker.py` on a `TrackerStore` bound to one open
transaction, so a decision or a review resolution is written whole or not at all.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import ValidationError

from ..models import InboxMessage
from ..ollama import RuntimeMetadata
from ..scheduled import MALAYSIA
from ..storage import AlreadyProcessedError
from .constants import JOB_ANALYSIS_INPUT_CONTRACT
from .models import (
    JobAnalysisOutput,
    JobApplication,
    JobStatus,
    present_company,
    present_job_title,
)
from .tracker import (
    Application,
    KeyPair,
    Link,
    LinkEffect,
    Outcome,
    ReviewCause,
    ReviewItem,
    StatusUpdate,
    apply_decision,
    find_exact,
    find_likely,
    key_pair,
    link_update,
    replay_status,
)

JOBS_LIVE_DIRECTORY = Path.home() / "mizu-kakeibo-private" / "jobs" / "live"
JOBS_LIVE_DATABASE = JOBS_LIVE_DIRECTORY / "job-tracker.sqlite3"

_STATUSES = "('applied', 'viewed', 'interview', 'rejected', 'offered')"

SCHEMA = f"""
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS messages (
    provider TEXT NOT NULL,
    message_id TEXT NOT NULL,
    thread_id TEXT,
    envelope_json TEXT NOT NULL,
    PRIMARY KEY (provider, message_id)
);

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    message_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    decision TEXT NOT NULL
        CHECK (decision IN ('status_update', 'not_application', 'needs_review')),
    reason TEXT NOT NULL,
    missing_fields_json TEXT NOT NULL,
    company TEXT,
    job_title TEXT,
    status TEXT CHECK (status IN {_STATUSES}),
    missing_value_replaced INTEGER NOT NULL CHECK (missing_value_replaced IN (0, 1)),
    grounding_replaced INTEGER NOT NULL CHECK (grounding_replaced IN (0, 1)),
    original_output TEXT,
    inbox_contract_version TEXT NOT NULL,
    analyser_input_contract_version TEXT NOT NULL,
    analyser_output_contract_version TEXT NOT NULL,
    runtime TEXT NOT NULL,
    runtime_version TEXT NOT NULL,
    model TEXT NOT NULL,
    model_digest TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    runtime_options_json TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    FOREIGN KEY (provider, message_id) REFERENCES messages(provider, message_id),
    UNIQUE (provider, message_id, revision)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_analysis_per_message
ON analyses(provider, message_id) WHERE active = 1;

CREATE TABLE IF NOT EXISTS analysis_failures (
    provider TEXT NOT NULL,
    message_id TEXT NOT NULL,
    failure_count INTEGER NOT NULL CHECK (failure_count > 0),
    last_failed_at TEXT NOT NULL,
    PRIMARY KEY (provider, message_id)
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    job_title TEXT NOT NULL,
    company_key TEXT NOT NULL,
    job_title_key TEXT NOT NULL,
    status TEXT CHECK (status IN {_STATUSES}),
    latest_update_date TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (company_key, job_title_key)
);

CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    company TEXT NOT NULL,
    job_title TEXT NOT NULL,
    company_key TEXT NOT NULL,
    job_title_key TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    UNIQUE (company_key, job_title_key)
);

CREATE TABLE IF NOT EXISTS review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL UNIQUE REFERENCES analyses(id),
    cause TEXT NOT NULL CHECK (cause IN ('analyser', 'likely_match', 'contradictory_outcome')),
    suggested_application_id INTEGER REFERENCES applications(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_item_id INTEGER NOT NULL UNIQUE REFERENCES review_items(id),
    action TEXT NOT NULL CHECK (action IN ('attach', 'match', 'create', 'not_application')),
    company TEXT,
    job_title TEXT,
    status TEXT CHECK (status IN {_STATUSES}),
    application_id INTEGER REFERENCES applications(id) ON DELETE SET NULL,
    resolved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS links (
    provider TEXT NOT NULL,
    message_id TEXT NOT NULL,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    analysis_id INTEGER NOT NULL REFERENCES analyses(id),
    resolution_id INTEGER REFERENCES resolutions(id),
    status TEXT NOT NULL CHECK (status IN {_STATUSES}),
    received_at TEXT NOT NULL,
    confirmed INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
    effect TEXT NOT NULL CHECK (effect IN ('created', 'advanced', 'reopened', 'unchanged')),
    linked_at TEXT NOT NULL,
    PRIMARY KEY (provider, message_id),
    FOREIGN KEY (provider, message_id) REFERENCES messages(provider, message_id)
);
"""


class JobReviewError(ValueError):
    """A review resolution the contract refuses; nothing was saved."""


class ReviewItemResolvedError(JobReviewError):
    """The review item is already resolved, or its analysis is no longer active."""


class LikelyMatchError(JobReviewError):
    """Matched values are a likely match, so the user must choose to attach or create."""

    def __init__(self, application: ApplicationRow) -> None:
        super().__init__(f"this looks like application #{application.id}")
        self.application = application


class ResolutionAction(str, Enum):
    ATTACH = "attach"
    MATCH = "match"
    CREATE = "create"
    NOT_APPLICATION = "not_application"


@dataclass(frozen=True)
class Extracted:
    """The company, job title, and status the analyser returned, any of which may be absent."""

    company: str | None = None
    job_title: str | None = None
    status: JobStatus | None = None


@dataclass(frozen=True)
class AnalysisRecord:
    """One analysis to persist: the decision to act on and what the analyser returned."""

    output: JobAnalysisOutput
    extracted: Extracted = Extracted()
    missing_value_replaced: bool = False
    grounding_replaced: bool = False
    original_output: str | None = None


@dataclass(frozen=True)
class ApplicationRow:
    id: int
    company: str
    job_title: str
    status: JobStatus
    latest_update: date


@dataclass(frozen=True)
class JobReviewItem:
    id: int
    provider: str
    message_id: str
    thread_id: str | None
    analysis_id: int
    cause: ReviewCause
    reason: str
    missing_fields: list[str]
    extracted: Extracted
    suggested: ApplicationRow | None
    created_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _application_row(row: sqlite3.Row) -> ApplicationRow:
    return ApplicationRow(
        id=row["id"],
        company=row["company"],
        job_title=row["job_title"],
        status=JobStatus(row["status"]),
        latest_update=date.fromisoformat(row["latest_update_date"]),
    )


class _Tracker:
    """The tracker store over one open transaction, for one analysis or one resolution."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        provider: str,
        analysis_id: int | None = None,
        resolution_id: int | None = None,
    ) -> None:
        self.connection = connection
        self.provider = provider
        self.analysis_id = analysis_id
        self.resolution_id = resolution_id

    def applications(self) -> Sequence[Application]:
        rows = self.connection.execute(
            "SELECT id, company, job_title, company_key, job_title_key FROM applications "
            "ORDER BY id"
        ).fetchall()
        return [
            Application(
                row["id"],
                row["company"],
                row["job_title"],
                (row["company_key"], row["job_title_key"]),
            )
            for row in rows
        ]

    def application(self, application_id: int) -> Application | None:
        return next(
            (
                application
                for application in self.applications()
                if application.id == application_id
            ),
            None,
        )

    def aliases(self, application_id: int) -> Sequence[KeyPair]:
        rows = self.connection.execute(
            "SELECT company_key, job_title_key FROM aliases WHERE application_id = ? ORDER BY id",
            (application_id,),
        ).fetchall()
        return [(row["company_key"], row["job_title_key"]) for row in rows]

    def links(self, application_id: int) -> Sequence[Link]:
        rows = self.connection.execute(
            "SELECT message_id, received_at, status, confirmed, effect FROM links "
            "WHERE application_id = ?",
            (application_id,),
        ).fetchall()
        return [
            Link(
                application_id,
                StatusUpdate(
                    row["message_id"],
                    datetime.fromisoformat(row["received_at"]),
                    JobStatus(row["status"]),
                    bool(row["confirmed"]),
                ),
                LinkEffect(row["effect"]),
            )
            for row in rows
        ]

    def status(self, application_id: int) -> JobStatus | None:
        row = self.connection.execute(
            "SELECT status FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        return JobStatus(row["status"]) if row is not None and row["status"] else None

    def create_application(self, company: str, job_title: str, pair: KeyPair) -> Application:
        cursor = self.connection.execute(
            "INSERT INTO applications(company, job_title, company_key, job_title_key, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (company, job_title, *pair, _utc_now()),
        )
        return Application(int(cursor.lastrowid), company, job_title, pair)

    def add_alias(self, application_id: int, company: str, job_title: str) -> None:
        self.connection.execute(
            "INSERT INTO aliases(application_id, company, job_title, company_key, job_title_key, "
            "confirmed_at) VALUES (?, ?, ?, ?, ?, ?)",
            (application_id, company, job_title, *key_pair(company, job_title), _utc_now()),
        )

    def add_link(self, link: Link, status: JobStatus) -> None:
        assert self.analysis_id is not None
        update = link.update
        self.connection.execute(
            "INSERT INTO links(provider, message_id, application_id, analysis_id, resolution_id, "
            "status, received_at, confirmed, effect, linked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.provider,
                update.message_id,
                link.application_id,
                self.analysis_id,
                self.resolution_id,
                update.status.value,
                update.received_at.isoformat(),
                int(update.confirmed),
                link.effect.value,
                _utc_now(),
            ),
        )
        self.refresh(link.application_id, status)

    def add_review_item(self, item: ReviewItem) -> None:
        assert self.analysis_id is not None
        self.connection.execute(
            "INSERT INTO review_items(analysis_id, cause, suggested_application_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (self.analysis_id, item.cause.value, item.suggested_application_id, _utc_now()),
        )

    def refresh(self, application_id: int, status: JobStatus) -> None:
        """Store an application's status and the Malaysia date of its newest linked message."""
        latest = max(link.update.received_at for link in self.links(application_id))
        self.connection.execute(
            "UPDATE applications SET status = ?, latest_update_date = ? WHERE id = ?",
            (status.value, latest.astimezone(MALAYSIA).date().isoformat(), application_id),
        )

    def withdraw(self, message_id: str) -> None:
        """Undo a message's unresolved review item and link before it is re-evaluated."""
        self.connection.execute(
            "DELETE FROM review_items WHERE id IN ("
            "SELECT ri.id FROM review_items ri JOIN analyses a ON a.id = ri.analysis_id "
            "LEFT JOIN resolutions r ON r.review_item_id = ri.id "
            "WHERE a.provider = ? AND a.message_id = ? AND r.id IS NULL)",
            (self.provider, message_id),
        )
        row = self.connection.execute(
            "SELECT application_id FROM links WHERE provider = ? AND message_id = ?",
            (self.provider, message_id),
        ).fetchone()
        if row is None:
            return
        application_id = row["application_id"]
        self.connection.execute(
            "DELETE FROM links WHERE provider = ? AND message_id = ?", (self.provider, message_id)
        )
        status = replay_status(link.update for link in self.links(application_id))
        if status is None:
            self.connection.execute("DELETE FROM applications WHERE id = ?", (application_id,))
        else:
            self.refresh(application_id, status)


def _read_only(database_path: Path) -> sqlite3.Connection:
    """Open the database read-only, so a missing one is never created, and without the run lock:
    in WAL mode a reader sees the last commit and never waits for a run.
    """
    uri = f"{Path(database_path).resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def read_counts(database_path: Path) -> tuple[int, int]:
    """Count the applications and the unresolved review items, writing nothing."""
    connection = _read_only(database_path)
    try:
        applications = connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        review = connection.execute(
            "SELECT COUNT(*) FROM review_items ri JOIN analyses a ON a.id = ri.analysis_id "
            "LEFT JOIN resolutions r ON r.review_item_id = ri.id "
            "WHERE a.active = 1 AND r.id IS NULL"
        ).fetchone()[0]
    finally:
        connection.close()
    return applications, review


@dataclass(frozen=True)
class NotApplication:
    """A message whose active analysis is `not_application`, as the skim list reads it."""

    provider: str
    message_id: str
    thread_id: str | None
    received_at: datetime
    sender: str
    subject: str


def read_not_applications(database_path: Path) -> list[NotApplication]:
    """Read the `not_application` messages, oldest first, writing nothing."""
    connection = _read_only(database_path)
    try:
        rows = connection.execute(
            "SELECT m.provider, m.message_id, m.thread_id, "
            "json_extract(m.envelope_json, '$.received_at'), "
            "json_extract(m.envelope_json, '$.sender'), "
            "json_extract(m.envelope_json, '$.subject') "
            "FROM analyses a JOIN messages m "
            "ON m.provider = a.provider AND m.message_id = a.message_id "
            "WHERE a.active = 1 AND a.decision = 'not_application'"
        ).fetchall()
    finally:
        connection.close()
    # Read only the fields the list shows, not every stored envelope's text.
    messages = [
        NotApplication(
            provider,
            message_id,
            thread_id,
            datetime.fromisoformat(received_at.replace("Z", "+00:00")),
            sender,
            subject,
        )
        for provider, message_id, thread_id, received_at, sender, subject in rows
    ]
    return sorted(messages, key=lambda message: (message.received_at, message.message_id))


# The application list's order: how far each application got, with closed ones last.
LIST_ORDER = (
    JobStatus.OFFERED,
    JobStatus.INTERVIEW,
    JobStatus.VIEWED,
    JobStatus.APPLIED,
    JobStatus.REJECTED,
)


def _listed(connection: sqlite3.Connection) -> list[ApplicationRow]:
    rows = connection.execute(
        "SELECT id, company, job_title, status, latest_update_date FROM applications"
    ).fetchall()
    applications = [_application_row(row) for row in rows]
    return sorted(
        applications,
        key=lambda application: (
            LIST_ORDER.index(application.status),
            -application.latest_update.toordinal(),
            application.id,
        ),
    )


def read_applications(database_path: Path) -> list[ApplicationRow]:
    """Read the applications in the list's order, writing nothing; number N is index N - 1."""
    connection = _read_only(database_path)
    connection.row_factory = sqlite3.Row
    try:
        return _listed(connection)
    finally:
        connection.close()


@dataclass(frozen=True)
class LinkedEmail:
    """A message linked to an application, as its history shows it."""

    provider: str
    message_id: str
    thread_id: str | None
    received_at: datetime
    sender: str
    subject: str
    status: JobStatus
    effect: LinkEffect
    reviewed: bool


@dataclass(frozen=True)
class ApplicationHistory:
    application: ApplicationRow
    aliases: list[tuple[str, str]]
    emails: list[LinkedEmail]


def read_history(database_path: Path, number: int) -> ApplicationHistory | None:
    """Read the application numbered `number` in the list, its aliases, and its emails oldest
    first, writing nothing; None when no application has that number.
    """
    connection = _read_only(database_path)
    connection.row_factory = sqlite3.Row
    try:
        applications = _listed(connection)
        if not 1 <= number <= len(applications):
            return None
        application = applications[number - 1]
        aliases = connection.execute(
            "SELECT company, job_title FROM aliases WHERE application_id = ? ORDER BY id",
            (application.id,),
        ).fetchall()
        rows = connection.execute(
            "SELECT l.provider, l.message_id, m.thread_id, l.received_at, l.status, l.effect, "
            "l.resolution_id, json_extract(m.envelope_json, '$.sender') AS sender, "
            "json_extract(m.envelope_json, '$.subject') AS subject "
            "FROM links l JOIN messages m "
            "ON m.provider = l.provider AND m.message_id = l.message_id "
            "WHERE l.application_id = ?",
            (application.id,),
        ).fetchall()
    finally:
        connection.close()
    emails = [
        LinkedEmail(
            row["provider"],
            row["message_id"],
            row["thread_id"],
            datetime.fromisoformat(row["received_at"]),
            row["sender"],
            row["subject"],
            JobStatus(row["status"]),
            LinkEffect(row["effect"]),
            row["resolution_id"] is not None,
        )
        for row in rows
    ]
    return ApplicationHistory(
        application,
        [(row["company"], row["job_title"]) for row in aliases],
        sorted(emails, key=lambda email: (email.received_at, email.message_id)),
    )


class JobStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
        os.chmod(self.database_path, 0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def is_processed(self, provider: str, message_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM analyses WHERE provider = ? AND message_id = ? AND active = 1",
                (provider, message_id),
            ).fetchone()
        return row is not None

    def filter_new_ids(self, provider: str, message_ids: list[str]) -> list[str]:
        """Return the IDs not yet processed by the job tracker, in order and without repeats."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT message_id FROM analyses WHERE provider = ? AND active = 1", (provider,)
            ).fetchall()
        processed = {row["message_id"] for row in rows}
        return [
            message_id for message_id in dict.fromkeys(message_ids) if message_id not in processed
        ]

    def latest_received_at(self, provider: str) -> datetime | None:
        """Return the newest received time among actively processed messages."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT json_extract(m.envelope_json, '$.received_at') AS received_at "
                "FROM messages m JOIN analyses a "
                "ON a.provider = m.provider AND a.message_id = m.message_id "
                "WHERE m.provider = ? AND a.active = 1",
                (provider,),
            ).fetchall()
        # Read only the received time, not every stored envelope's text.
        received = [
            datetime.fromisoformat(row["received_at"].replace("Z", "+00:00")) for row in rows
        ]
        return max(received, default=None)

    def record_failure(self, provider: str, message_id: str) -> int:
        """Count a failed analysis of the message's own and return its failure count."""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO analysis_failures(provider, message_id, failure_count, "
                "last_failed_at) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(provider, message_id) DO UPDATE SET "
                "failure_count = failure_count + 1, last_failed_at = excluded.last_failed_at",
                (provider, message_id, _utc_now()),
            )
            row = connection.execute(
                "SELECT failure_count FROM analysis_failures WHERE provider = ? AND message_id = ?",
                (provider, message_id),
            ).fetchone()
        return int(row["failure_count"])

    def save_analysis(
        self,
        envelope: InboxMessage,
        record: AnalysisRecord,
        metadata: RuntimeMetadata,
        *,
        reevaluate: bool = False,
    ) -> Outcome:
        """Store one analysis and apply its decision, in one transaction."""
        provider = envelope.source.provider
        message_id = envelope.source.message_id
        output, extracted = record.output, record.extracted
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT id, revision FROM analyses "
                "WHERE provider = ? AND message_id = ? AND active = 1",
                (provider, message_id),
            ).fetchone()
            if active is not None and not reevaluate:
                raise AlreadyProcessedError(f"{provider}:{message_id} is already processed")
            revision = (active["revision"] + 1) if active is not None else 1
            connection.execute(
                "INSERT INTO messages(provider, message_id, thread_id, envelope_json) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(provider, message_id) DO UPDATE SET "
                "thread_id = excluded.thread_id, envelope_json = excluded.envelope_json",
                (provider, message_id, envelope.source.thread_id, envelope.model_dump_json()),
            )
            if active is not None:
                connection.execute("UPDATE analyses SET active = 0 WHERE id = ?", (active["id"],))
                _Tracker(connection, provider).withdraw(message_id)
            cursor = connection.execute(
                """
                INSERT INTO analyses(
                    provider, message_id, revision, active, decision, reason,
                    missing_fields_json, company, job_title, status, missing_value_replaced,
                    grounding_replaced, original_output, inbox_contract_version,
                    analyser_input_contract_version, analyser_output_contract_version, runtime,
                    runtime_version, model, model_digest, prompt_version, runtime_options_json,
                    processed_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    message_id,
                    revision,
                    output.decision.value,
                    output.reason,
                    json.dumps([field.value for field in output.missing_fields]),
                    extracted.company,
                    extracted.job_title,
                    extracted.status.value if extracted.status else None,
                    int(record.missing_value_replaced),
                    int(record.grounding_replaced),
                    record.original_output,
                    envelope.contract_version,
                    JOB_ANALYSIS_INPUT_CONTRACT,
                    output.contract_version,
                    metadata.runtime,
                    metadata.runtime_version,
                    metadata.model,
                    metadata.model_digest,
                    metadata.prompt_version,
                    json.dumps(
                        {
                            "temperature": metadata.temperature,
                            "seed": metadata.seed,
                            "context": metadata.context_size,
                            "max_generated_tokens": metadata.max_generated_tokens,
                            "streaming": False,
                            "tools": [],
                        },
                        sort_keys=True,
                    ),
                    _utc_now(),
                ),
            )
            tracker = _Tracker(connection, provider, int(cursor.lastrowid))
            return apply_decision(tracker, message_id, envelope.received_at, output)

    def message(self, provider: str, message_id: str) -> InboxMessage:
        """Return the stored envelope of a processed message."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT envelope_json FROM messages WHERE provider = ? AND message_id = ?",
                (provider, message_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"{provider}:{message_id} is not stored")
        return InboxMessage.model_validate_json(row["envelope_json"])

    def list_applications(self) -> list[ApplicationRow]:
        """Return every application in the list's order."""
        with self.connect() as connection:
            return _listed(connection)

    def match(self, company: str, job_title: str) -> tuple[str, ApplicationRow | None]:
        """Say what matching these names would do: `exact`, `likely`, or `new`."""
        pair = key_pair(company, job_title)
        with self.connect() as connection:
            tracker = _Tracker(connection, "gmail")
            exact = find_exact(tracker, pair)
            if exact is not None:
                return "exact", self._row(connection, exact.id)
            likely = find_likely(tracker, pair)
            if likely is not None:
                return "likely", self._row(connection, likely.id)
        return "new", None

    @staticmethod
    def _row(connection: sqlite3.Connection, application_id: int) -> ApplicationRow:
        row = connection.execute(
            "SELECT id, company, job_title, status, latest_update_date FROM applications "
            "WHERE id = ?",
            (application_id,),
        ).fetchone()
        return _application_row(row)

    def list_review_items(self) -> list[JobReviewItem]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT ri.id, ri.cause, ri.suggested_application_id, ri.created_at,
                       a.id AS analysis_id, a.provider, a.message_id, a.reason,
                       a.missing_fields_json, a.company, a.job_title, a.status, m.thread_id
                FROM review_items ri
                JOIN analyses a ON a.id = ri.analysis_id
                JOIN messages m ON m.provider = a.provider AND m.message_id = a.message_id
                LEFT JOIN resolutions r ON r.review_item_id = ri.id
                WHERE a.active = 1 AND r.id IS NULL
                ORDER BY a.processed_at, ri.id
                """
            ).fetchall()
            suggested = {
                row["suggested_application_id"]: self._row(
                    connection, row["suggested_application_id"]
                )
                for row in rows
                if row["suggested_application_id"] is not None
            }
        return [
            JobReviewItem(
                id=row["id"],
                provider=row["provider"],
                message_id=row["message_id"],
                thread_id=row["thread_id"],
                analysis_id=row["analysis_id"],
                cause=ReviewCause(row["cause"]),
                reason=row["reason"],
                missing_fields=json.loads(row["missing_fields_json"]),
                extracted=Extracted(
                    present_company(row["company"]),
                    present_job_title(row["job_title"]),
                    JobStatus(row["status"]) if row["status"] else None,
                ),
                suggested=suggested.get(row["suggested_application_id"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def resolve_review(
        self,
        review_item_id: int,
        action: ResolutionAction,
        *,
        application_id: int | None = None,
        company: str | None = None,
        job_title: str | None = None,
        status: JobStatus | None = None,
    ) -> ApplicationRow | None:
        """Resolve one review item under contract §8 and return the application it links to.

        Attach takes an application and a status, and optionally both names, which become an
        alias when they are new. Match and create take both names and a status.
        """
        if action is ResolutionAction.NOT_APPLICATION:
            if any(value is not None for value in (application_id, company, job_title, status)):
                raise JobReviewError("not an application takes no values")
        else:
            if status is None:
                raise JobReviewError(f"{action.value} needs a status")
            if (company is None) != (job_title is None):
                raise JobReviewError("give both the company and the job title, or neither")
            if action is not ResolutionAction.ATTACH and company is None:
                raise JobReviewError(f"{action.value} needs a company and a job title")
            if (action is ResolutionAction.ATTACH) != (application_id is not None):
                raise JobReviewError("attach needs an application, and only attach takes one")
            if company is not None and job_title is not None:
                try:
                    JobApplication(company=company, job_title=job_title, status=status)
                except ValidationError as error:
                    raise JobReviewError(error.errors()[0]["msg"]) from None
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT ri.analysis_id, a.provider, a.message_id, m.envelope_json
                FROM review_items ri
                JOIN analyses a ON a.id = ri.analysis_id
                JOIN messages m ON m.provider = a.provider AND m.message_id = a.message_id
                LEFT JOIN resolutions r ON r.review_item_id = ri.id
                WHERE ri.id = ? AND a.active = 1 AND r.id IS NULL
                """,
                (review_item_id,),
            ).fetchone()
            if row is None:
                raise ReviewItemResolvedError("no unresolved review item with this ID")
            cursor = connection.execute(
                "INSERT INTO resolutions(review_item_id, action, company, job_title, status, "
                "resolved_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    review_item_id,
                    action.value,
                    company,
                    job_title,
                    status.value if status else None,
                    _utc_now(),
                ),
            )
            resolution_id = int(cursor.lastrowid)
            if action is ResolutionAction.NOT_APPLICATION or status is None:
                return None
            tracker = _Tracker(connection, row["provider"], row["analysis_id"], resolution_id)
            application = self._resolved_application(
                tracker, action, application_id, company, job_title
            )
            received_at = InboxMessage.model_validate_json(row["envelope_json"]).received_at
            link_update(
                tracker, application, StatusUpdate(row["message_id"], received_at, status, True)
            )
            connection.execute(
                "UPDATE resolutions SET application_id = ? WHERE id = ?",
                (application.id, resolution_id),
            )
            return self._row(connection, application.id)

    def _resolved_application(
        self,
        tracker: _Tracker,
        action: ResolutionAction,
        application_id: int | None,
        company: str | None,
        job_title: str | None,
    ) -> Application:
        if action is ResolutionAction.ATTACH:
            application = tracker.application(application_id) if application_id else None
            if application is None:
                raise JobReviewError(f"there is no application #{application_id}")
            # Attach may leave out the names, and then it saves no alias.
            if company is None or job_title is None:
                return application
            owner = find_exact(tracker, key_pair(company, job_title))
            if owner is None:
                tracker.add_alias(application.id, company, job_title)
            elif owner.id != application.id:
                raise JobReviewError(
                    f"that company and job title belong to application #{owner.id}"
                )
            return application
        assert company is not None and job_title is not None
        pair = key_pair(company, job_title)
        owner = find_exact(tracker, pair)
        if action is ResolutionAction.CREATE:
            if owner is not None:
                raise JobReviewError(
                    f"that company and job title are already application #{owner.id}"
                )
            return tracker.create_application(company, job_title, pair)
        if owner is not None:
            return owner
        likely = find_likely(tracker, pair)
        if likely is not None:
            raise LikelyMatchError(self._row(tracker.connection, likely.id))
        return tracker.create_application(company, job_title, pair)
