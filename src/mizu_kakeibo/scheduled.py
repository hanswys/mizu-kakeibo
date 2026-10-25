from __future__ import annotations

import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .constants import OLLAMA_BASE_URL
from .gmail_api import GmailRetrievalError, GmailSignInError
from .ollama import AnalysisError
from .runlock import RunLockHeldError, run_lock

MALAYSIA = timezone(timedelta(hours=8))
SYNC_LABEL = "com.mizu-kakeibo.sync"
OLLAMA_LABEL = "com.mizu-kakeibo.ollama"
OLLAMA_AGENT = Path.home() / "Library/LaunchAgents" / f"{OLLAMA_LABEL}.plist"
INSTALLED_PROGRAM = Path.home() / ".local/bin/mizu-kakeibo"
SCHEDULE_HOURS = range(8, 24)
MINIMUM_BATTERY_PERCENT = 20
RUNTIME_START_SECONDS = 30
RECORD_DAYS = 90
RUN_RECORD = "run.json"
RUN_NAME_FORMAT = "%Y%m%d-%H%M%S"
REVIEW_SCRIPT = "review.command"
_RUN_NAME = re.compile(r"^(\d{8}-\d{6})(?:-\d+)?$")
GROUPS = ("expense", "not_expense", "needs_review", "skipped", "filtered", "failed", "deferred")
ANALYSED = ("expense", "not_expense", "needs_review", "failed")
NOTIFIED = ("expense", "needs_review", "failed")  # a run with only non-expenses is silent
OSASCRIPT = "/usr/bin/osascript"
TITLE = "Mizu Kakeibo"
DIALOG_SECONDS = 3600
# Every value reaches osascript as an argument, never as script text.
NOTIFICATION_SCRIPT = "\n".join(
    [
        "on run argv",
        "display notification (item 3 of argv) with title (item 1 of argv)"
        " subtitle (item 2 of argv)",
        "end run",
    ]
)
REVIEW_DIALOG_SCRIPT = "\n".join(
    [
        "on run argv",
        "set reply to display dialog (item 2 of argv) with title (item 1 of argv)"
        ' buttons {"Dismiss", "Review in Terminal"} default button "Review in Terminal"'
        " giving up after (item 3 of argv as integer)",
        'if button returned of reply is "Review in Terminal" then'
        ' do shell script "/usr/bin/open -a Terminal " & quoted form of (item 4 of argv)',
        "end run",
    ]
)
FAILURE_DIALOG_SCRIPT = "\n".join(
    [
        "on run argv",
        "display dialog (item 2 of argv) with title (item 1 of argv)"
        ' buttons {"Dismiss"} default button "Dismiss" with icon caution'
        " giving up after (item 3 of argv as integer)",
        "end run",
    ]
)
JOB_TITLE = "Mizu Kakeibo Jobs"
JOB_ANALYSED = ("status_update", "not_application", "review", "failed")
JOB_UPDATES = ("created", "advanced", "reopened")  # a step with only unchanged links is silent
JOB_FAILURE_SUBTITLE = "Job sync stopped"
JOB_FAILURE_TEXTS = {
    "sign_in": 'The job sync can\'t read Gmail. Run "mizu-kakeibo gmail-sign-in" in Terminal.',
    "runtime": "The job sync couldn't use the analyser (Ollama). It will try again next hour.",
    "retrieval": "The job sync couldn't search Gmail. It will try again next hour.",
    "unexpected": "The job sync stopped with an unexpected error. "
    "The details are in launchd.err.log beside the expense database.",
}
FAILURE_DIALOGS = {
    "sign_in": "The sync can't read Gmail because the sign-in has expired or was removed. "
    'Run "mizu-kakeibo gmail-sign-in" in Terminal.',
    "runtime": "The sync couldn't start the analyser (Ollama). It will try again next hour.",
    "retrieval": "The sync couldn't search Gmail. It will try again next hour.",
    "unexpected": "The sync stopped with an unexpected error. "
    "The details are in launchd.err.log beside the database.",
}


@dataclass(frozen=True)
class SyncReport:
    """A sync's `process` report, with its summary and per-message results."""

    output: dict[str, Any]
    runtime_version: str
    flagged: list[str] = field(default_factory=list)  # the new expenses that were flagged


@dataclass(frozen=True)
class WakeState:
    lid_closed: bool | None
    capabilities: list[str] | None

    @property
    def full(self) -> bool:
        return (
            self.lid_closed is False
            and self.capabilities is not None
            and "Graphics" in self.capabilities
        )


@dataclass(frozen=True)
class PowerState:
    source: str | None
    battery_percent: int | None

    @property
    def sufficient(self) -> bool:
        if self.source == "ac":
            return True
        return (
            self.source == "battery"
            and self.battery_percent is not None
            and self.battery_percent >= MINIMUM_BATTERY_PERCENT
        )


def parse_clamshell(output: str) -> bool | None:
    """Read whether the lid is closed from `ioreg -r -k AppleClamshellState -d 1`."""
    match = re.search(r'"AppleClamshellState" = (Yes|No)\b', output)
    return None if match is None else match.group(1) == "Yes"


def parse_capabilities(output: str) -> list[str] | None:
    """Read the system capabilities from `pmset -g systemstate`."""
    match = re.search(r"Current System Capabilities are:(.*)", output)
    return None if match is None else match.group(1).split()


def parse_power(output: str) -> PowerState:
    """Read the power source and battery charge from `pmset -g batt`."""
    source = re.search(r"Now drawing from '([^']+)'", output)
    percent = re.search(r"(\d+)%;", output)
    sources = {"AC Power": "ac", "Battery Power": "battery"}
    return PowerState(
        sources.get(source.group(1)) if source else None,
        int(percent.group(1)) if percent else None,
    )


class System:
    """The macOS state a scheduled run reads and the services it starts."""

    def _output(self, *command: str) -> str:
        try:
            return subprocess.run(
                command, capture_output=True, text=True, timeout=10, check=False
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def wake_state(self) -> WakeState:
        return WakeState(
            parse_clamshell(
                self._output("/usr/sbin/ioreg", "-r", "-k", "AppleClamshellState", "-d", "1")
            ),
            parse_capabilities(self._output("/usr/bin/pmset", "-g", "systemstate")),
        )

    def power_state(self) -> PowerState:
        return parse_power(self._output("/usr/bin/pmset", "-g", "batt"))

    def runtime_version(self) -> str | None:
        try:
            response = httpx.get(f"{OLLAMA_BASE_URL}/api/version", timeout=3)
            response.raise_for_status()
            version = response.json().get("version")
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        return version if isinstance(version, str) else None

    def start_runtime(self) -> str:
        """Start the Ollama LaunchAgent and return the launchctl subcommand used."""
        domain = f"gui/{os.getuid()}"
        service = f"{domain}/{OLLAMA_LABEL}"
        loaded = (
            subprocess.run(["/bin/launchctl", "print", service], capture_output=True).returncode
            == 0
        )
        command = ["kickstart", service] if loaded else ["bootstrap", domain, str(OLLAMA_AGENT)]
        result = subprocess.run(["/bin/launchctl", *command], capture_output=True)
        if result.returncode != 0:
            raise AnalysisError(
                f"cannot start the analyser runtime: launchctl {command[0]} "
                f"exited {result.returncode}"
            )
        return command[0]

    @contextmanager
    def keep_awake(self) -> Iterator[None]:
        """Hold off idle sleep until this process exits, even if it is killed."""
        caffeinate = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
        try:
            yield
        finally:
            caffeinate.terminate()
            caffeinate.wait()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def notify(self, subtitle: str, text: str, title: str = TITLE) -> None:
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [OSASCRIPT, "-e", NOTIFICATION_SCRIPT, title, subtitle, text],
                capture_output=True,
                timeout=10,
                check=False,
            )

    def show_dialog(self, text: str, review_script: Path | None) -> None:
        """Show a dialog without waiting for it.

        launchd kills a job's remaining process group when the job exits, and starts no
        new instance while it runs, so the dialog gets its own session and outlives the run.
        """
        script = FAILURE_DIALOG_SCRIPT if review_script is None else REVIEW_DIALOG_SCRIPT
        arguments = [TITLE, text, str(DIALOG_SECONDS)]
        if review_script is not None:
            arguments.append(str(review_script))
        with suppress(OSError):
            subprocess.Popen(
                [OSASCRIPT, "-e", script, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )


@dataclass
class RuntimeStart:
    version: str | None = None
    started_with: str | None = None


def ensure_runtime(system: System, start: RuntimeStart) -> None:
    """Start the analyser runtime when it does not answer, waiting up to 30 seconds."""
    start.version = system.runtime_version()
    if start.version is not None:
        return
    start.started_with = system.start_runtime()
    for _ in range(RUNTIME_START_SECONDS):
        system.sleep(1)
        start.version = system.runtime_version()
        if start.version is not None:
            return
    raise AnalysisError(
        f"the analyser runtime did not answer within {RUNTIME_START_SECONDS} seconds of starting"
    )


def sanitized_error(error: str) -> str:
    """Keep an error's fixed text; a normalization error may quote the message, so drop it."""
    if error.startswith("normalization failed"):
        return "normalization failed"
    return " ".join(error.split())[:200]


def _run_started(directory: Path) -> datetime | None:
    match = _RUN_NAME.match(directory.name)
    if match is None or not directory.is_dir():
        return None
    return datetime.strptime(match.group(1), RUN_NAME_FORMAT).replace(tzinfo=MALAYSIA)


def _scheduled_runs(runs: Path) -> list[tuple[datetime, Path]]:
    """Return scheduled run directories, newest first. Manual run directories are ignored."""
    if not runs.is_dir():
        return []
    found = []
    for directory in runs.iterdir():
        started = _run_started(directory)
        if started is not None and (directory / RUN_RECORD).is_file():
            found.append((started, directory))
    return sorted(found, key=lambda run: run[1].name, reverse=True)


def _records(runs: Path) -> Iterator[tuple[datetime, dict[str, Any]]]:
    for started, directory in _scheduled_runs(runs):
        try:
            record = json.loads((directory / RUN_RECORD).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            yield started, record


def failure_dialog_shown(runs: Path, today: datetime) -> bool:
    return any(
        started.date() == today.date() and record.get("dialog") == "failure"
        for started, record in _records(runs)
    )


def _count(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def notification_text(counts: dict[str, int], flagged: int = 0) -> tuple[str, str]:
    """Return a notification's subtitle and text from a run's counts and flagged expenses."""
    checked = sum(counts[group] for group in ANALYSED)
    text = (
        f"{_count(counts['expense'], 'expense')} · {counts['needs_review']} to review · "
        f"{counts['failed']} failed"
    )
    return (
        f"Checked {_count(checked, 'new message')}",
        text + (f" · {flagged} flagged" if flagged else ""),
    )


def review_dialog_text(counts: dict[str, int]) -> str:
    text = (
        f"Sync finished: {_count(counts['expense'], 'expense')}, "
        f"{counts['needs_review']} to review, {counts['failed']} failed."
    )
    if counts["failed"]:
        text += " Failed messages are retried next hour."
    return text


def needs_full_search(runs: Path) -> bool:
    """Search from the sync start when the last scheduled run that processed had failures."""
    for _, record in _records(runs):
        groups = record.get("groups")
        if isinstance(groups, dict):
            return bool(groups.get("failed"))
    return False


def delete_old_records(runs: Path, log: Path, now: datetime) -> None:
    cutoff = now - timedelta(days=RECORD_DAYS)
    for started, directory in _scheduled_runs(runs):
        if started < cutoff:
            shutil.rmtree(directory)
    if not log.is_file():
        return
    lines = log.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [line for line in lines if not _logged_before(line, cutoff)]
    if len(kept) != len(lines):
        replacement = log.with_name(f"{log.name}.tmp")
        _write_private(replacement, "".join(kept))
        os.replace(replacement, log)


def _logged_before(line: str, cutoff: datetime) -> bool:
    try:
        logged = datetime.fromisoformat(line.split(" ", 1)[0])
    except ValueError:
        return False
    return logged.tzinfo is not None and logged < cutoff


def write_review_script(program: Path, database: Path, ledger: Path) -> Path:
    """Write the script that Terminal runs to start `review`, beside the database.

    It holds absolute, shell-quoted paths only, and is replaced atomically.
    """
    program, database, ledger = (
        Path(os.path.abspath(path.expanduser())) for path in (program, database, ledger)
    )
    command = shlex.join(
        [str(program), "review", "--database", str(database), "--ledger", str(ledger)]
    )
    path = database.parent / REVIEW_SCRIPT
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{REVIEW_SCRIPT}.")
    try:
        os.fchmod(descriptor, 0o700)
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(f"#!/bin/sh\nexec {command}\n")
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return path


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        target.write(text)


def _append_log(log: Path, started: datetime, fields: dict[str, Any]) -> str:
    line = " ".join(
        [
            started.isoformat(timespec="seconds"),
            *(f"{key}={value}" for key, value in fields.items()),
        ]
    )
    log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as target:
        target.write(line + "\n")
    print(line, flush=True)
    return line


def _new_run_directory(runs: Path, started: datetime) -> Path:
    name = started.strftime(RUN_NAME_FORMAT)
    runs.mkdir(mode=0o700, parents=True, exist_ok=True)
    for suffix in range(100):
        directory = runs / (name if suffix == 0 else f"{name}-{suffix}")
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            continue
        return directory
    raise FileExistsError(f"too many runs started at {name}")


def _yes_no(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "no")


def _now() -> datetime:
    return datetime.now(MALAYSIA)


@dataclass(frozen=True)
class Failure:
    """Why a step, or the shared checks before both steps, stopped before processing."""

    stage: str
    error: str
    unexpected: Exception | None = None  # raised after the run so launchd's error file has it


def _message_errors(results: list[dict[str, Any]]) -> dict[str, str]:
    return {
        result["message_id"]: sanitized_error(result["error"])
        for result in results
        if result["status"] == "failed" and result["error"]
    }


def _failure(error: Exception) -> Failure:
    if isinstance(error, GmailSignInError):
        return Failure("sign_in", str(error))
    if isinstance(error, AnalysisError):
        return Failure("runtime", str(error))
    if isinstance(error, GmailRetrievalError):
        return Failure("retrieval", str(error))
    return Failure("unexpected", type(error).__name__, error)


@dataclass(frozen=True)
class RunResult:
    status: int
    record: dict[str, Any] | None = None
    unexpected: Exception | None = None


@dataclass(frozen=True)
class SharedChecks:
    """What the gates and shared checks found, for the expense step's record."""

    trigger: str
    started: datetime
    wake: WakeState
    power: PowerState
    runtime: RuntimeStart
    failure: Failure | None


def scheduled_sync(
    database: Path,
    jobs_database: Path,
    *,
    check_sign_in: Callable[[], object],
    verify_runtime: Callable[[], object],
    sync: Callable[[bool], SyncReport],
    jobs_sync: Callable[[], dict[str, Any]],
    system: System,
    write_review_script: Callable[[], Path],
) -> int:
    """Run one scheduled sync: the shared gates and checks once, then the expense and job steps.

    `sync(full)` runs the expense Gmail API sync and `jobs_sync()` the job tracker's, each while
    its step holds that app's run lock. A held lock or a failure in one step does not stop the
    other; a failed sign-in or runtime check stops both and is reported by the expense step.
    Each step shows its notification or dialog after releasing its lock.
    """
    live = Path(database).parent
    runs, log = live / "runs", live / "scheduled.log"
    started = _now().astimezone(MALAYSIA)
    trigger = "launchd" if os.environ.get("XPC_SERVICE_NAME") == SYNC_LABEL else "command"
    wake = system.wake_state()
    power = system.power_state() if wake.full else None
    gate = _gate_stop(wake, power)
    if gate is not None:
        # No step runs, but old expense records are still deleted when the lock is free.
        with suppress(RunLockHeldError), run_lock(database):
            delete_old_records(runs, log, started)
        _append_log(log, started, {"trigger": trigger, **gate})
        return 0
    assert power is not None  # read once the wake gate passed

    runtime = RuntimeStart()
    with system.keep_awake():
        failure: Failure | None = None
        try:
            check_sign_in()
            ensure_runtime(system, runtime)
            verify_runtime()
        except Exception as error:
            failure = _failure(error)
        shared = SharedChecks(trigger, started, wake, power, runtime, failure)
        expense = _expense_step(database, runs, log, shared, sync)
        if expense.record is not None:
            _report(expense.record, system, write_review_script)
        jobs = RunResult(0)
        if failure is None:
            jobs = _jobs_step(jobs_database, trigger, jobs_sync)
            if jobs.record is not None:
                _report_jobs(jobs.record, system)

    unexpected = [
        error
        for error in (failure.unexpected if failure else None, expense.unexpected, jobs.unexpected)
        if error is not None
    ]
    for error in unexpected[1:]:
        traceback.print_exception(type(error), error, error.__traceback__)
    if unexpected:
        raise unexpected[0]
    return max(expense.status, jobs.status, 0 if failure is None else 1)


def _gate_stop(wake: WakeState, power: PowerState | None) -> dict[str, str] | None:
    """Return the log fields of the first gate that fails, or None when both pass."""
    if not wake.full:
        graphics = None if wake.capabilities is None else "Graphics" in wake.capabilities
        return {
            "outcome": "not_full_wake",
            "lid_closed": _yes_no(wake.lid_closed),
            "graphics": _yes_no(graphics),
        }
    if power is not None and not power.sufficient:
        percent = "unknown" if power.battery_percent is None else f"{power.battery_percent}%"
        return {"outcome": "low_power", "power": power.source or "unknown", "battery": percent}
    return None


def _report(
    record: dict[str, Any], system: System, write_review_script: Callable[[], Path]
) -> None:
    counts = record["counts"]
    if record["notification"]:
        system.notify(*notification_text(counts, len(record["flagged"] or ())))
    if record["dialog"] == "review":
        system.show_dialog(review_dialog_text(counts), write_review_script())
    elif record["dialog"] == "failure":
        system.show_dialog(FAILURE_DIALOGS[record["failed_stage"]], None)


def _append_locked(log: Path, started: datetime, trigger: str, error: RunLockHeldError) -> None:
    holder = (error.holder or {}).get("pid", "unknown")
    _append_log(log, started, {"trigger": trigger, "outcome": "locked", "holder_pid": holder})


def _expense_step(
    database: Path,
    runs: Path,
    log: Path,
    shared: SharedChecks,
    sync: Callable[[bool], SyncReport],
) -> RunResult:
    try:
        with run_lock(database):
            return _locked_expense_step(runs, log, shared, sync)
    except RunLockHeldError as error:
        _append_locked(log, shared.started, shared.trigger, error)
        return RunResult(0)


def _locked_expense_step(
    runs: Path,
    log: Path,
    shared: SharedChecks,
    sync: Callable[[bool], SyncReport],
) -> RunResult:
    started, wake, power, runtime = shared.started, shared.wake, shared.power, shared.runtime
    delete_old_records(runs, log, started)
    full = needs_full_search(runs)
    report: SyncReport | None = None
    failure, own = shared.failure, None
    if failure is None:
        try:
            report = sync(full)
        except Exception as error:
            failure = own = _failure(error)

    groups = (
        None if report is None else {group: report.output["summary"][group] for group in GROUPS}
    )
    message_errors = {} if report is None else _message_errors(report.output["results"])
    counts = None if groups is None else {group: len(ids) for group, ids in groups.items()}
    if counts is None:
        notification = False
        dialog = None if failure_dialog_shown(runs, started) else "failure"
    else:
        notification = any(counts[group] for group in NOTIFIED)
        dialog = "review" if counts["needs_review"] or counts["failed"] else None
    record = {
        "trigger": shared.trigger,
        "started_at": started.isoformat(timespec="seconds"),
        "ended_at": _now().astimezone(MALAYSIA).isoformat(timespec="seconds"),
        "wake": {
            "lid_closed": wake.lid_closed,
            "capabilities": wake.capabilities,
            "power_source": power.source,
            "battery_percent": power.battery_percent,
        },
        "runtime_version": report.runtime_version if report is not None else runtime.version,
        "runtime_started_with": runtime.started_with,
        "full_search": full,
        "outcome": "synced" if report is not None else "failed",
        "failed_stage": None if failure is None else failure.stage,
        "error": None if failure is None else sanitized_error(failure.error),
        "groups": groups,
        "counts": counts,
        "message_errors": message_errors,
        "flagged": None if report is None else report.flagged,
        "notification": notification,
        "dialog": dialog,
    }
    directory = _new_run_directory(runs, started)
    _write_private(directory / RUN_RECORD, json.dumps(record, indent=2) + "\n")

    fields: dict[str, Any] = {
        "trigger": shared.trigger,
        "outcome": record["outcome"],
        "run": directory.name,
    }
    if groups is None:
        fields["stage"] = record["failed_stage"]
    else:
        fields.update(record["counts"])
    _append_log(log, started, fields)
    status = 1 if groups is None or groups["failed"] else 0
    return RunResult(status, record, None if own is None else own.unexpected)


def job_failure_notified(runs: Path, today: datetime) -> bool:
    return any(
        started.date() == today.date() and record.get("notification") == "failure"
        for started, record in _records(runs)
    )


def job_notification_text(counts: dict[str, int], applications: dict[str, int]) -> tuple[str, str]:
    """Return a job notification's subtitle and text from a job step's counts."""
    checked = sum(counts[group] for group in JOB_ANALYSED)
    updates = sum(applications[effect] for effect in JOB_UPDATES)
    return (
        f"Checked {_count(checked, 'new message')}",
        f"{_count(updates, 'update')} · {counts['review']} to review · {counts['failed']} failed",
    )


def _report_jobs(record: dict[str, Any], system: System) -> None:
    if record["notification"] == "update":
        system.notify(
            *job_notification_text(record["counts"], record["applications"]), title=JOB_TITLE
        )
    elif record["notification"] == "failure":
        system.notify(
            JOB_FAILURE_SUBTITLE, JOB_FAILURE_TEXTS[record["failed_stage"]], title=JOB_TITLE
        )


def _jobs_step(
    jobs_database: Path, trigger: str, jobs_sync: Callable[[], dict[str, Any]]
) -> RunResult:
    live = Path(jobs_database).parent
    runs, log = live / "runs", live / "scheduled.log"
    started = _now().astimezone(MALAYSIA)
    try:
        with run_lock(jobs_database):
            return _locked_jobs_step(runs, log, started, trigger, jobs_sync)
    except RunLockHeldError as error:
        _append_locked(log, started, trigger, error)
        return RunResult(0)


def _locked_jobs_step(
    runs: Path,
    log: Path,
    started: datetime,
    trigger: str,
    jobs_sync: Callable[[], dict[str, Any]],
) -> RunResult:
    delete_old_records(runs, log, started)
    report: dict[str, Any] | None = None
    failure: Failure | None = None
    try:
        report = jobs_sync()
    except Exception as error:
        failure = _failure(error)

    if report is None:
        groups = counts = applications = None
        notification = None if job_failure_notified(runs, started) else "failure"
    else:
        groups, applications = report["summary"], report["applications"]
        counts = {group: len(ids) for group, ids in groups.items()}
        changed = any(applications[effect] for effect in JOB_UPDATES)
        notification = "update" if changed or counts["review"] or counts["failed"] else None
    record = {
        "trigger": trigger,
        "started_at": started.isoformat(timespec="seconds"),
        "ended_at": _now().astimezone(MALAYSIA).isoformat(timespec="seconds"),
        "outcome": "synced" if report is not None else "failed",
        "failed_stage": None if failure is None else failure.stage,
        "error": None if failure is None else sanitized_error(failure.error),
        "groups": groups,
        "counts": counts,
        "already_processed": None if report is None else report["already_processed"],
        "applications": applications,
        "skim": None if report is None else [item["message_id"] for item in report["skim"]],
        "message_errors": {} if report is None else _message_errors(report["results"]),
        "notification": notification,
    }
    directory = _new_run_directory(runs, started)
    _write_private(directory / RUN_RECORD, json.dumps(record, indent=2) + "\n")

    fields: dict[str, Any] = {
        "trigger": trigger,
        "outcome": record["outcome"],
        "run": directory.name,
    }
    if counts is None:
        fields["stage"] = record["failed_stage"]
    else:
        fields.update(counts)
        fields["already_processed"] = record["already_processed"]
    _append_log(log, started, fields)
    status = 1 if counts is None or counts["failed"] else 0
    return RunResult(status, record, None if failure is None else failure.unexpected)


def launch_agent(program: Path, database: Path, ledger: Path, gmail_directory: Path) -> bytes:
    """Build the LaunchAgent that runs the installed `scheduled-sync` every hour."""
    # Absolute without resolving links, so the plist keeps naming uv's command link.
    paths = [
        Path(os.path.abspath(path.expanduser()))
        for path in (program, database, ledger, gmail_directory)
    ]
    program, database, ledger, gmail_directory = paths
    if not program.is_file():
        raise ValueError(
            f"{program} does not exist; install the command with `uv tool install` first"
        )
    live = database.parent
    return plistlib.dumps(
        {
            "Label": SYNC_LABEL,
            "ProgramArguments": [
                str(program),
                "scheduled-sync",
                "--database",
                str(database),
                "--ledger",
                str(ledger),
                "--gmail-directory",
                str(gmail_directory),
            ],
            "StartCalendarInterval": [{"Hour": hour, "Minute": 0} for hour in SCHEDULE_HOURS],
            "ProcessType": "Background",
            "StandardOutPath": str(live / "launchd.out.log"),
            "StandardErrorPath": str(live / "launchd.err.log"),
        }
    )
