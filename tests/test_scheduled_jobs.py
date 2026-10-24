"""The job step of `scheduled-sync`, on a fake system, a mocked Gmail API, and a scripted Ollama.

Every message is fictional. The expense step reads the same mocked inbox, so these tests also
check that each step runs, fails, and reports independently of the other.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from jobs_fakes import ScriptedOllama, other, update
from test_gmail_api import (  # noqa: F401 - the autouse fixture guards against real Google calls
    FakeAnalyser,
    FakeTokenEndpoint,
    no_google_token_requests,
    write_token,
)
from test_jobs_sync import ANSWERS, CAREERS, DIGEST, LINKEDIN, at, gmail_message
from test_jobs_sync import FakeGmail as JobsFakeGmail
from test_scheduled import Clock, FakeSystem

from mizu_kakeibo import cli, gmail_api, scheduled
from mizu_kakeibo.jobs.storage import JobStore
from mizu_kakeibo.scheduled import FAILURE_DIALOGS, JOB_FAILURE_TEXTS, MALAYSIA

STORE = "Example Store <receipts@example.invalid>"
SUBJECTS = ("Your application was sent", "Interview invitation", "Your application digest")
SECRET = "Fictional words that must never be recorded"


class Inbox(JobsFakeGmail):
    """The job tests' mocked Gmail, able to refuse either step's search alone.

    Only the job search lists job words, so `refuse_search` picks a step by its query.
    """

    def __init__(self, messages) -> None:
        super().__init__(messages)
        self.refused_searches: dict[str, int] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            step = "jobs" if "(" in request.url.params["q"] else "expense"
            status = self.refused_searches.get(step)
            if status is not None:
                self.requests.append(f"refused:{step}")
                return httpx.Response(status, json={"error": {"code": status}})
        return super().handler(request)


class System(FakeSystem):
    """Record every check and report in order, to show what runs once and in which step."""

    def __init__(self, database, jobs_database) -> None:
        super().__init__(database, jobs_database)
        self.events: list[str] = []

    def wake_state(self):
        self.events.append("wake")
        return super().wake_state()

    def power_state(self):
        self.events.append("power")
        return super().power_state()

    def runtime_version(self):
        self.events.append("runtime")
        return super().runtime_version()

    def notify(self, subtitle, text, title=scheduled.TITLE):
        self.events.append(f"notify:{title}")
        super().notify(subtitle, text, title)

    def show_dialog(self, text, review_script):
        self.events.append("dialog")
        super().show_dialog(text, review_script)


@pytest.fixture
def hour(tmp_path, monkeypatch):
    directory = tmp_path / "gmail-api"
    write_token(directory)
    live, jobs_live = tmp_path / "live", tmp_path / "jobs" / "live"
    inbox = Inbox(
        [
            gmail_message("j1", at(1), LINKEDIN, SUBJECTS[0]),
            gmail_message("j2", at(3), CAREERS, SUBJECTS[1]),
            gmail_message("j3", at(5), DIGEST, SUBJECTS[2]),
        ]
    )
    expense_analyser = FakeAnalyser()
    ollama = ScriptedOllama(dict(ANSWERS))
    system = System(live / "kakeibo.sqlite3", jobs_live / "job-tracker.sqlite3")
    clock = Clock()
    monkeypatch.setattr(cli, "OllamaAnalyser", lambda: expense_analyser)
    monkeypatch.setattr(cli, "OllamaJobAnalyser", ollama.analyser)
    monkeypatch.setattr(
        cli, "GmailApiClient", lambda creds: gmail_api.GmailApiClient(creds, client=inbox.client())
    )
    monkeypatch.setattr(cli, "System", lambda: system)
    monkeypatch.setattr(scheduled, "_now", clock)
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    argv = [
        "scheduled-sync",
        "--gmail-directory",
        str(directory),
        "--database",
        str(live / "kakeibo.sqlite3"),
        "--ledger",
        str(live / "ledger.csv"),
        "--jobs-database",
        str(jobs_live / "job-tracker.sqlite3"),
    ]

    def records(directory):
        runs = directory / "runs"
        if not runs.is_dir():
            return []
        return [json.loads((path / "run.json").read_text()) for path in sorted(runs.iterdir())]

    def lines(directory):
        path = directory / "scheduled.log"
        return path.read_text().splitlines() if path.exists() else []

    def add_receipt(message_id="r1"):
        """A receipt only the expense step reads: it has no job word."""
        inbox.messages[message_id] = gmail_message(
            message_id, at(10), STORE, "Receipt", html="<p>Thanks for your purchase.</p>"
        )

    return SimpleNamespace(
        argv=argv,
        live=live,
        jobs_live=jobs_live,
        jobs_database=jobs_live / "job-tracker.sqlite3",
        inbox=inbox,
        ollama=ollama,
        expense_analyser=expense_analyser,
        system=system,
        clock=clock,
        directory=directory,
        records=lambda: records(live),
        log=lambda: lines(live),
        job_records=lambda: records(jobs_live),
        job_log=lambda: lines(jobs_live),
        add_receipt=add_receipt,
        main=lambda: cli.main(argv),
    )


def test_one_hour_runs_the_shared_checks_once_then_the_expense_step_then_the_job_step(
    hour, capsys
) -> None:
    hour.add_receipt()
    assert hour.main() == 0

    events = hour.system.events
    assert events == ["wake", "power", "runtime", "notify:Mizu Kakeibo", "notify:Mizu Kakeibo Jobs"]
    assert hour.system.kept_awake == 1
    assert hour.system.shown_under_lock == 0
    [expense] = hour.records()
    assert expense["groups"]["expense"] == ["r1"]
    [expense_line] = hour.log()
    [job_line] = hour.job_log()
    assert capsys.readouterr().out == f"{expense_line}\n{job_line}\n"
    assert job_line == (
        "2026-10-17T10:00:04+08:00 trigger=command outcome=synced run=20261017-100004 "
        "status_update=2 not_application=1 review=0 skipped=0 failed=0 deferred=0 "
        "already_processed=0"
    )
    # The receipt has no job word, so the job step never analyses it.
    assert hour.ollama.calls == list(SUBJECTS)


def test_the_job_step_writes_its_own_record_without_message_content(hour) -> None:
    assert hour.main() == 0
    [record] = hour.job_records()
    assert record == {
        "trigger": "command",
        "started_at": "2026-10-17T10:00:04+08:00",
        "ended_at": "2026-10-17T10:00:04+08:00",
        "outcome": "synced",
        "failed_stage": None,
        "error": None,
        "groups": {
            "status_update": ["j1", "j2"],
            "not_application": ["j3"],
            "review": [],
            "skipped": [],
            "failed": [],
            "deferred": [],
        },
        "counts": {
            "status_update": 2,
            "not_application": 1,
            "review": 0,
            "skipped": 0,
            "failed": 0,
            "deferred": 0,
        },
        "already_processed": 0,
        "applications": {"created": 1, "advanced": 1, "reopened": 0, "unchanged": 0},
        "skim": ["j3"],
        "message_errors": {},
        "notification": "update",
    }
    run_directory = hour.jobs_live / "runs" / "20261017-100004"
    assert run_directory.stat().st_mode & 0o777 == 0o700
    assert (run_directory / "run.json").stat().st_mode & 0o777 == 0o600
    assert (hour.jobs_live / "scheduled.log").stat().st_mode & 0o777 == 0o600
    written = "".join(
        path.read_text() for path in hour.jobs_live.rglob("*") if path.suffix in {".json", ".log"}
    )
    for private in (*SUBJECTS, "Example Company", "Software Engineer", "careers@example.invalid"):
        assert private not in written


def test_a_job_step_that_changed_applications_notifies_its_counts_without_a_dialog(hour) -> None:
    assert hour.main() == 0
    assert hour.system.job_notifications == [
        ("Checked 3 new messages", "2 updates · 0 to review · 0 failed")
    ]
    assert hour.system.notifications == []
    assert hour.system.dialogs == []


def test_a_job_review_item_notifies_but_opens_no_dialog(hour) -> None:
    hour.ollama.answers[SUBJECTS[1]] = other("needs_review", ["job_title"])
    assert hour.main() == 0
    assert hour.job_records()[0]["notification"] == "update"
    assert hour.system.job_notifications == [
        ("Checked 3 new messages", "1 update · 1 to review · 0 failed")
    ]
    assert hour.system.dialogs == []


def test_a_job_step_with_only_unchanged_links_and_non_applications_is_silent(hour) -> None:
    assert hour.main() == 0
    hour.system.job_notifications.clear()
    # A repeated status links to its application without changing it.
    hour.inbox.messages["j4"] = gmail_message("j4", at(6), CAREERS, "Interview reminder")
    hour.ollama.answers["Interview reminder"] = update(
        "Example Company", "Software Engineer", "interview"
    )
    hour.inbox.messages["j5"] = gmail_message("j5", at(7), DIGEST, "Weekly applications")
    hour.ollama.answers["Weekly applications"] = other("not_application")
    hour.clock.time += timedelta(hours=1)

    assert hour.main() == 0
    second = hour.job_records()[1]
    assert second["groups"]["status_update"] == ["j4"]
    assert second["applications"]["unchanged"] == 1
    assert second["notification"] is None
    assert hour.system.job_notifications == []

    hour.clock.time += timedelta(hours=1)
    assert hour.main() == 0  # nothing new
    assert hour.job_records()[2]["already_processed"] == 5
    assert hour.system.job_notifications == []


def test_a_failed_job_message_notifies_and_is_found_again_next_hour(hour) -> None:
    hour.inbox.refusals["raw:j1"] = [httpx.Response(500, json={"error": {"code": 500}})]
    assert hour.main() == 1
    first = hour.job_records()[0]
    assert first["groups"]["failed"] == ["j1"]
    assert first["message_errors"] == {"j1": "Gmail retrieval error: Gmail API returned HTTP 500"}
    assert hour.system.job_notifications == [
        ("Checked 3 new messages", "1 update · 0 to review · 1 failed")
    ]
    assert hour.system.dialogs == []

    hour.clock.time += timedelta(hours=1)
    assert hour.main() == 0
    assert hour.job_records()[1]["groups"]["status_update"] == ["j1"]


def test_the_job_step_defers_beyond_its_batch_and_continues_next_hour(hour, monkeypatch) -> None:
    monkeypatch.setattr(cli, "JOBS_SYNC_BATCH_LIMIT", 2)
    assert hour.main() == 0
    first = hour.job_records()[0]
    assert first["groups"]["status_update"] == ["j1", "j2"]
    assert first["groups"]["deferred"] == ["j3"]

    hour.clock.time += timedelta(hours=1)
    assert hour.main() == 0
    second = hour.job_records()[1]
    assert second["groups"]["not_application"] == ["j3"]
    assert second["already_processed"] == 2


def test_the_job_step_selects_at_most_25_messages_by_default(hour) -> None:
    for day in range(10, 40):
        hour.inbox.messages[f"n{day}"] = gmail_message(f"n{day}", at(day), CAREERS, SUBJECTS[2])
    assert hour.main() == 0
    groups = hour.job_records()[0]["groups"]
    assert len(groups["status_update"]) + len(groups["not_application"]) == 25
    assert groups["status_update"] == ["j1", "j2"]  # oldest first
    assert len(groups["deferred"]) == 8


def test_a_held_job_lock_skips_only_the_job_step(hour, hold_run_lock) -> None:
    holder = hold_run_lock(hour.jobs_database)
    hour.add_receipt()
    assert hour.main() == 0
    assert hour.job_log() == [
        f"2026-10-17T10:00:04+08:00 trigger=command outcome=locked holder_pid={holder.pid}"
    ]
    assert hour.job_records() == []
    assert hour.ollama.calls == []
    assert hour.system.job_notifications == []
    [expense] = hour.records()
    assert expense["groups"]["expense"] == ["r1"]
    assert hour.system.notifications == [
        ("Checked 1 new message", "1 expense · 0 to review · 0 failed")
    ]


def test_a_held_expense_lock_skips_only_the_expense_step(hour, hold_run_lock) -> None:
    holder = hold_run_lock(hour.live / "kakeibo.sqlite3")
    hour.add_receipt()
    assert hour.main() == 0
    assert hour.log() == [
        f"2026-10-17T10:00:04+08:00 trigger=command outcome=locked holder_pid={holder.pid}"
    ]
    assert hour.records() == []
    assert hour.expense_analyser.inputs == []
    assert hour.system.notifications == hour.system.dialogs == []
    assert hour.job_records()[0]["groups"]["status_update"] == ["j1", "j2"]
    assert len(hour.system.job_notifications) == 1


def test_a_failed_expense_search_does_not_stop_the_job_step(hour) -> None:
    hour.inbox.refused_searches["expense"] = 503
    assert hour.main() == 1
    [expense] = hour.records()
    assert expense["failed_stage"] == "retrieval"
    assert hour.system.dialogs == [(FAILURE_DIALOGS["retrieval"], None)]
    [job] = hour.job_records()
    assert job["outcome"] == "synced"
    assert job["groups"]["status_update"] == ["j1", "j2"]


def test_an_unexpected_expense_error_does_not_stop_the_job_step(hour, monkeypatch) -> None:
    def broken(*_, **__):
        raise KeyError(SECRET)

    monkeypatch.setattr(cli, "run_sync", broken)
    with pytest.raises(KeyError):
        hour.main()
    assert hour.records()[0]["failed_stage"] == "unexpected"
    assert hour.job_records()[0]["outcome"] == "synced"
    assert len(hour.system.job_notifications) == 1


def test_a_failed_job_search_notifies_once_a_day_and_leaves_the_expense_step(hour) -> None:
    hour.add_receipt()
    hour.inbox.refused_searches["jobs"] = 503
    assert hour.main() == 1
    [expense] = hour.records()
    assert expense["groups"]["expense"] == ["r1"]
    assert hour.system.notifications == [
        ("Checked 1 new message", "1 expense · 0 to review · 0 failed")
    ]
    [job] = hour.job_records()
    assert job["outcome"] == "failed"
    assert job["failed_stage"] == "retrieval"
    assert job["groups"] is job["counts"] is job["skim"] is None
    assert job["notification"] == "failure"
    assert hour.job_log()[-1].endswith("outcome=failed run=20261017-100004 stage=retrieval")
    assert hour.system.job_notifications == [("Job sync stopped", JOB_FAILURE_TEXTS["retrieval"])]
    assert hour.system.dialogs == []

    hour.clock.time += timedelta(hours=1)
    assert hour.main() == 1
    assert hour.job_records()[1]["notification"] is None
    assert len(hour.system.job_notifications) == 1

    hour.clock.time = datetime(2026, 10, 18, 8, 0, 4, tzinfo=MALAYSIA)
    assert hour.main() == 1
    assert len(hour.system.job_notifications) == 2


def test_an_unexpected_job_error_is_recorded_by_type_and_raised_after_the_run(
    hour, monkeypatch
) -> None:
    def broken(*_, **__):
        raise KeyError(SECRET)

    monkeypatch.setattr(cli, "run_jobs_sync", broken)
    hour.add_receipt()
    with pytest.raises(KeyError):
        hour.main()
    assert hour.records()[0]["groups"]["expense"] == ["r1"]
    [job] = hour.job_records()
    assert job["failed_stage"] == "unexpected"
    assert job["error"] == "KeyError"
    assert hour.system.job_notifications == [("Job sync stopped", JOB_FAILURE_TEXTS["unexpected"])]
    assert SECRET not in (hour.jobs_live / "scheduled.log").read_text()


def test_unexpected_errors_in_both_steps_are_both_recorded(hour, monkeypatch, capsys) -> None:
    def broken(kind):
        def fail(*_, **__):
            raise kind("fictional")

        return fail

    monkeypatch.setattr(cli, "run_sync", broken(KeyError))
    monkeypatch.setattr(cli, "run_jobs_sync", broken(LookupError))
    with pytest.raises(KeyError):
        hour.main()
    assert hour.records()[0]["error"] == "KeyError"
    assert hour.job_records()[0]["error"] == "LookupError"
    assert "LookupError" in capsys.readouterr().err


@pytest.mark.parametrize("stage", ["sign_in", "runtime"])
def test_a_failed_shared_check_stops_both_steps_with_one_expense_dialog(
    hour, monkeypatch, stage
) -> None:
    if stage == "sign_in":
        write_token(hour.directory, expiry="2026-01-01T00:00:00Z")
        endpoint = FakeTokenEndpoint(400, {"error": "invalid_grant"})
        monkeypatch.setattr(gmail_api, "_refresh_request", lambda: endpoint)
    else:
        hour.system.versions = [None]
    assert hour.main() == 1
    [expense] = hour.records()
    assert expense["failed_stage"] == stage
    assert hour.system.dialogs == [(FAILURE_DIALOGS[stage], None)]
    assert hour.inbox.requests == []
    assert hour.ollama.calls == []
    assert hour.job_records() == []
    assert hour.job_log() == []
    assert hour.system.job_notifications == []
    assert not hour.jobs_database.exists()


def test_a_gate_stop_runs_neither_step(hour) -> None:
    hour.system.power = scheduled.PowerState("battery", 5)
    assert hour.main() == 0
    assert hour.log()[-1].endswith("outcome=low_power power=battery battery=5%")
    assert hour.inbox.requests == []
    assert hour.job_log() == []
    assert hour.system.kept_awake == 0


def test_job_records_and_log_lines_older_than_90_days_are_deleted(hour) -> None:
    old = hour.clock.time - timedelta(days=91)
    recent = hour.clock.time - timedelta(days=89)
    for started in (old, recent):
        directory = hour.jobs_live / "runs" / started.strftime(scheduled.RUN_NAME_FORMAT)
        directory.mkdir(parents=True)
        (directory / "run.json").write_text("{}")
    (hour.jobs_live / "scheduled.log").write_text(
        f"{old.isoformat(timespec='seconds')} trigger=launchd outcome=synced\n"
        f"{recent.isoformat(timespec='seconds')} trigger=launchd outcome=synced\n"
    )
    assert hour.main() == 0
    remaining = sorted(path.name for path in (hour.jobs_live / "runs").iterdir())
    assert remaining == [recent.strftime(scheduled.RUN_NAME_FORMAT), "20261017-100004"]
    assert [line.split()[0] for line in hour.job_log()] == [
        recent.isoformat(timespec="seconds"),
        "2026-10-17T10:00:04+08:00",
    ]


def test_manual_syncs_never_notify(hour, capsys) -> None:
    hour.add_receipt()
    gmail = ["--gmail-directory", str(hour.directory)]
    jobs = ["jobs-sync", *gmail, "--database", str(hour.jobs_database)]
    assert cli.main(jobs) == 0
    assert cli.main(["sync", *gmail, *hour.argv[3:7]]) == 0
    capsys.readouterr()
    assert hour.system.events == []
    assert hour.job_records() == hour.records() == []
    assert JobStore(hour.jobs_database).filter_new_ids("gmail", ["j1"]) == []
