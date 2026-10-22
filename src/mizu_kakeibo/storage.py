from __future__ import annotations

import csv
import fcntl
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .models import AnalysisDecision, ExpenseAnalysisOutput, InboxMessage, Transaction
from .ollama import RuntimeMetadata

LIVE_DIRECTORY = Path.home() / "mizu-kakeibo-private" / "live"
LIVE_DATABASE = LIVE_DIRECTORY / "kakeibo.sqlite3"
LIVE_LEDGER = LIVE_DIRECTORY / "ledger.csv"


class AlreadyProcessedError(RuntimeError):
    pass


class ReviewResolutionError(ValueError):
    pass


class LedgerCorrectionError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewItem:
    provider: str
    message_id: str
    thread_id: str | None
    analysis_id: int
    reason: str
    missing_fields: list[str]
    created_at: str


@dataclass(frozen=True)
class LedgerEntry:
    provider: str
    message_id: str
    merchant: str
    amount_minor: int
    currency: str
    transaction_date: date


@dataclass(frozen=True)
class LedgerCorrection:
    previous: Transaction
    corrected: Transaction

    @property
    def changed(self) -> bool:
        return self.previous != self.corrected


SCHEMA = """
PRAGMA foreign_keys = ON;
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
    decision TEXT NOT NULL CHECK (decision IN ('expense', 'not_expense', 'needs_review')),
    reason TEXT NOT NULL,
    missing_fields_json TEXT NOT NULL,
    merchant TEXT,
    amount_minor INTEGER,
    currency TEXT,
    transaction_date TEXT,
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

CREATE TABLE IF NOT EXISTS resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL UNIQUE,
    decision TEXT NOT NULL CHECK (decision IN ('expense', 'not_expense')),
    merchant TEXT,
    amount_minor INTEGER,
    currency TEXT,
    transaction_date TEXT,
    resolved_at TEXT NOT NULL,
    FOREIGN KEY (analysis_id) REFERENCES analyses(id)
);

CREATE TABLE IF NOT EXISTS transactions (
    provider TEXT NOT NULL,
    message_id TEXT NOT NULL,
    analysis_id INTEGER NOT NULL,
    merchant TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL,
    transaction_date TEXT NOT NULL,
    PRIMARY KEY (provider, message_id),
    FOREIGN KEY (analysis_id) REFERENCES analyses(id)
);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL,
    merchant TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL,
    transaction_date TEXT NOT NULL,
    corrected_at TEXT NOT NULL,
    FOREIGN KEY (analysis_id) REFERENCES analyses(id)
);

CREATE TABLE IF NOT EXISTS removals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL UNIQUE,
    removed_at TEXT NOT NULL,
    FOREIGN KEY (analysis_id) REFERENCES analyses(id)
);

CREATE TABLE IF NOT EXISTS analysis_failures (
    provider TEXT NOT NULL,
    message_id TEXT NOT NULL,
    failure_count INTEGER NOT NULL CHECK (failure_count > 0),
    last_failed_at TEXT NOT NULL,
    PRIMARY KEY (provider, message_id)
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, database_path: Path, ledger_path: Path) -> None:
        self.database_path = Path(database_path)
        self.ledger_path = Path(ledger_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
        os.chmod(self.database_path, 0o600)
        self.export_ledger()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
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
        if not message_ids:
            return []
        unique_ids = list(dict.fromkeys(message_ids))
        placeholders = ",".join("?" for _ in unique_ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT message_id FROM analyses WHERE provider = ? AND active = 1 "
                f"AND message_id IN ({placeholders})",
                (provider, *unique_ids),
            ).fetchall()
        processed = {row["message_id"] for row in rows}
        return [message_id for message_id in unique_ids if message_id not in processed]

    def latest_received_at(self, provider: str) -> datetime | None:
        """Return the newest received time among actively processed messages."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT m.envelope_json FROM messages m JOIN analyses a "
                "ON a.provider = m.provider AND a.message_id = m.message_id "
                "WHERE m.provider = ? AND a.active = 1",
                (provider,),
            ).fetchall()
        received = [
            InboxMessage.model_validate_json(row["envelope_json"]).received_at for row in rows
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
        output: ExpenseAnalysisOutput,
        metadata: RuntimeMetadata,
        *,
        reevaluate: bool = False,
    ) -> int:
        provider = envelope.source.provider
        message_id = envelope.source.message_id
        transaction = output.transaction
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
                (
                    provider,
                    message_id,
                    envelope.source.thread_id,
                    envelope.model_dump_json(),
                ),
            )
            if active is not None:
                connection.execute("UPDATE analyses SET active = 0 WHERE id = ?", (active["id"],))
            cursor = connection.execute(
                """
                INSERT INTO analyses(
                    provider, message_id, revision, active, decision, reason,
                    missing_fields_json, merchant, amount_minor, currency, transaction_date,
                    inbox_contract_version, analyser_input_contract_version,
                    analyser_output_contract_version, runtime, runtime_version, model,
                    model_digest, prompt_version, runtime_options_json, processed_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    message_id,
                    revision,
                    output.decision.value,
                    output.reason,
                    json.dumps([field.value for field in output.missing_fields]),
                    transaction.merchant if transaction else None,
                    transaction.amount_minor if transaction else None,
                    transaction.currency if transaction else None,
                    transaction.transaction_date.isoformat() if transaction else None,
                    envelope.contract_version,
                    "expense-analysis-input/v0",
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
            analysis_id = int(cursor.lastrowid)
            connection.execute(
                "DELETE FROM transactions WHERE provider = ? AND message_id = ?",
                (provider, message_id),
            )
            if transaction is not None:
                self._insert_transaction(connection, provider, message_id, analysis_id, transaction)
        self.export_ledger()
        return analysis_id

    @staticmethod
    def _insert_transaction(
        connection: sqlite3.Connection,
        provider: str,
        message_id: str,
        analysis_id: int,
        transaction: Transaction,
    ) -> None:
        connection.execute(
            "INSERT INTO transactions(provider, message_id, analysis_id, merchant, "
            "amount_minor, currency, transaction_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                provider,
                message_id,
                analysis_id,
                transaction.merchant,
                transaction.amount_minor,
                transaction.currency,
                transaction.transaction_date.isoformat(),
            ),
        )

    def list_review_items(self) -> list[ReviewItem]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.provider, a.message_id, m.thread_id, a.id, a.reason,
                       a.missing_fields_json, a.processed_at
                FROM analyses a
                JOIN messages m ON m.provider = a.provider AND m.message_id = a.message_id
                LEFT JOIN resolutions r ON r.analysis_id = a.id
                WHERE a.active = 1 AND a.decision = 'needs_review' AND r.id IS NULL
                ORDER BY a.processed_at, a.id
                """
            ).fetchall()
        return [
            ReviewItem(
                provider=row["provider"],
                message_id=row["message_id"],
                thread_id=row["thread_id"],
                analysis_id=row["id"],
                reason=row["reason"],
                missing_fields=json.loads(row["missing_fields_json"]),
                created_at=row["processed_at"],
            )
            for row in rows
        ]

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

    def list_transactions(self, start: date, end: date) -> list[LedgerEntry]:
        """Return the logged expenses dated from `start` to `end`, both inclusive."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT provider, message_id, merchant, amount_minor, currency, "
                "transaction_date FROM transactions "
                "WHERE transaction_date BETWEEN ? AND ? "
                "ORDER BY transaction_date, provider, message_id",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [
            LedgerEntry(
                provider=row["provider"],
                message_id=row["message_id"],
                merchant=row["merchant"],
                amount_minor=row["amount_minor"],
                currency=row["currency"],
                transaction_date=date.fromisoformat(row["transaction_date"]),
            )
            for row in rows
        ]

    def resolve_review(
        self,
        provider: str,
        message_id: str,
        decision: AnalysisDecision,
        transaction: Transaction | None = None,
    ) -> None:
        if decision is AnalysisDecision.NEEDS_REVIEW:
            raise ReviewResolutionError("a review must resolve to expense or not_expense")
        if (decision is AnalysisDecision.EXPENSE) != (transaction is not None):
            raise ReviewResolutionError("expense requires a transaction; not_expense forbids one")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            analysis = connection.execute(
                """
                SELECT a.id FROM analyses a
                LEFT JOIN resolutions r ON r.analysis_id = a.id
                WHERE a.provider = ? AND a.message_id = ? AND a.active = 1
                  AND a.decision = 'needs_review' AND r.id IS NULL
                """,
                (provider, message_id),
            ).fetchone()
            if analysis is None:
                raise ReviewResolutionError("no unresolved active review item for this source")
            connection.execute(
                """
                INSERT INTO resolutions(
                    analysis_id, decision, merchant, amount_minor, currency,
                    transaction_date, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis["id"],
                    decision.value,
                    transaction.merchant if transaction else None,
                    transaction.amount_minor if transaction else None,
                    transaction.currency if transaction else None,
                    transaction.transaction_date.isoformat() if transaction else None,
                    _utc_now(),
                ),
            )
            if transaction is not None:
                self._insert_transaction(
                    connection, provider, message_id, analysis["id"], transaction
                )
        self.export_ledger()

    def correct_transaction(
        self,
        provider: str,
        message_id: str,
        *,
        merchant: str | None = None,
        amount_minor: int | None = None,
        currency: str | None = None,
        transaction_date: date | None = None,
    ) -> LedgerCorrection:
        """Correct a logged expense, keeping its analysis and any review resolution."""
        changes = {
            key: value
            for key, value in {
                "merchant": merchant,
                "amount_minor": amount_minor,
                "currency": currency,
                "transaction_date": transaction_date,
            }.items()
            if value is not None
        }
        if not changes:
            raise LedgerCorrectionError("a correction must name at least one field")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT analysis_id, merchant, amount_minor, currency, transaction_date "
                "FROM transactions WHERE provider = ? AND message_id = ?",
                (provider, message_id),
            ).fetchone()
            if row is None:
                raise LedgerCorrectionError("no logged expense for this source")
            previous = Transaction(
                merchant=row["merchant"],
                amount_minor=row["amount_minor"],
                currency=row["currency"],
                transaction_date=date.fromisoformat(row["transaction_date"]),
            )
            corrected = Transaction(**{**previous.model_dump(), **changes})
            correction = LedgerCorrection(previous=previous, corrected=corrected)
            if not correction.changed:
                return correction
            connection.execute(
                """
                INSERT INTO corrections(
                    analysis_id, merchant, amount_minor, currency, transaction_date,
                    corrected_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["analysis_id"],
                    corrected.merchant,
                    corrected.amount_minor,
                    corrected.currency,
                    corrected.transaction_date.isoformat(),
                    _utc_now(),
                ),
            )
            connection.execute(
                "UPDATE transactions SET merchant = ?, amount_minor = ?, currency = ?, "
                "transaction_date = ? WHERE provider = ? AND message_id = ?",
                (
                    corrected.merchant,
                    corrected.amount_minor,
                    corrected.currency,
                    corrected.transaction_date.isoformat(),
                    provider,
                    message_id,
                ),
            )
        self.export_ledger()
        return correction

    def remove_transaction(self, provider: str, message_id: str) -> Transaction:
        """Mark a logged expense as not an expense, keeping its analysis and corrections."""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT analysis_id, merchant, amount_minor, currency, transaction_date "
                "FROM transactions WHERE provider = ? AND message_id = ?",
                (provider, message_id),
            ).fetchone()
            if row is None:
                raise LedgerCorrectionError("no logged expense for this source")
            connection.execute(
                "INSERT INTO removals(analysis_id, removed_at) VALUES (?, ?)",
                (row["analysis_id"], _utc_now()),
            )
            connection.execute(
                "DELETE FROM transactions WHERE provider = ? AND message_id = ?",
                (provider, message_id),
            )
        self.export_ledger()
        return Transaction(
            merchant=row["merchant"],
            amount_minor=row["amount_minor"],
            currency=row["currency"],
            transaction_date=date.fromisoformat(row["transaction_date"]),
        )

    def export_ledger(self) -> None:
        lock_path = self.ledger_path.with_name(f".{self.ledger_path.name}.lock")
        with lock_path.open("a+b") as lock:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            with self.connect() as connection:
                rows = connection.execute(
                    "SELECT provider, message_id, merchant, amount_minor, currency, "
                    "transaction_date FROM transactions "
                    "ORDER BY transaction_date, provider, message_id"
                ).fetchall()
            handle, temporary_name = tempfile.mkstemp(
                dir=self.ledger_path.parent,
                prefix=f".{self.ledger_path.name}.",
                suffix=".tmp",
                text=True,
            )
            try:
                with os.fdopen(handle, "w", newline="", encoding="utf-8") as output:
                    writer = csv.writer(output)
                    writer.writerow(
                        [
                            "source_provider",
                            "gmail_message_id",
                            "merchant",
                            "amount_minor",
                            "currency",
                            "transaction_date",
                        ]
                    )
                    for row in rows:
                        writer.writerow(tuple(row))
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary_name, self.ledger_path)
                os.chmod(self.ledger_path, 0o600)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
