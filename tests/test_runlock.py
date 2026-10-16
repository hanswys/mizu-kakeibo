from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys

import pytest

from mizu_kakeibo.runlock import RunLockHeldError, run_lock, run_lock_path

_OPEN_NEW_DATABASE = """
import sys, time
from pathlib import Path
from mizu_kakeibo.runlock import RunLockHeldError, run_lock
from mizu_kakeibo.storage import Store
database, ledger, start = map(Path, sys.argv[1:])
print("ready", flush=True)
while not start.exists():
    time.sleep(0.005)
try:
    with run_lock(database):
        Store(database, ledger)
        time.sleep(0.5)
except RunLockHeldError:
    print("held")
else:
    print("ran")
"""


def test_lock_held_by_another_process_is_refused(tmp_path, hold_run_lock) -> None:
    database = tmp_path / "private.sqlite3"
    holder = hold_run_lock(database)

    with pytest.raises(RunLockHeldError) as error, run_lock(database):
        pass
    assert error.value.holder is not None
    assert error.value.holder["pid"] == holder.pid
    assert "started_at" in error.value.holder


def test_lock_is_free_again_after_its_holder_is_killed(tmp_path, hold_run_lock) -> None:
    database = tmp_path / "private.sqlite3"
    holder = hold_run_lock(database)
    holder.kill()
    holder.wait()

    with run_lock(database):
        holder_record = json.loads(run_lock_path(database).read_text(encoding="utf-8"))
        assert holder_record["pid"] == os.getpid()


def test_lock_is_released_on_exit_and_owner_only(tmp_path) -> None:
    database = tmp_path / "private.sqlite3"
    with run_lock(database):
        pass
    with run_lock(database):
        pass
    lock_path = run_lock_path(database)
    assert lock_path.name == "private.sqlite3.lock"
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_two_runs_opening_a_new_database_do_not_collide(tmp_path) -> None:
    database = tmp_path / "live" / "private.sqlite3"
    ledger = tmp_path / "live" / "ledger.csv"
    start = tmp_path / "start"
    runs = [
        subprocess.Popen(
            [sys.executable, "-c", _OPEN_NEW_DATABASE, str(database), str(ledger), str(start)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    for run in runs:
        assert run.stdout is not None
        assert run.stdout.readline() == "ready\n"
    start.touch()

    outcomes = []
    for run in runs:
        stdout, stderr = run.communicate(timeout=30)
        assert run.returncode == 0, stderr
        outcomes.append(stdout.strip())
    assert sorted(outcomes) == ["held", "ran"]
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"analyses", "analysis_failures"} <= tables
