from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunLockHeldError(RuntimeError):
    """Another processing run holds the lock on this database."""

    def __init__(self, holder: dict[str, Any] | None) -> None:
        super().__init__("another run is in progress")
        self.holder = holder


def run_lock_path(database_path: Path) -> Path:
    database_path = Path(database_path)
    return database_path.with_name(f"{database_path.name}.lock")


def _read_holder(lock_path: Path) -> dict[str, Any] | None:
    try:
        holder = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return holder if isinstance(holder, dict) else None


@contextmanager
def run_lock(database_path: Path) -> Iterator[None]:
    """Hold the database's run lock without waiting, or raise RunLockHeldError.

    The operating system releases a flock when its holder exits, so a crashed run leaves
    no stale lock. The file is never deleted, because a new file would be a second lock.
    """
    lock_path = run_lock_path(database_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RunLockHeldError(_read_holder(lock_path)) from error
        holder = {"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}
        os.ftruncate(descriptor, 0)
        os.pwrite(descriptor, (json.dumps(holder) + "\n").encode("utf-8"), 0)
        yield
    finally:
        os.close(descriptor)
