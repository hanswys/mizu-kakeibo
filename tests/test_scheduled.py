from __future__ import annotations

import json
import plistlib
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from test_gmail_api import (  # noqa: F401 - the autouse fixture guards against real Google calls
    SNIPPET,
    FakeAnalyser,
    FakeGmail,
    FakeTokenEndpoint,
    api_message,
    no_google_token_requests,
    write_token,
)

from mizu_kakeibo import cli, gmail_api, scheduled
from mizu_kakeibo.constants import MODEL, MODEL_DIGEST, OLLAMA_BASE_URL
from mizu_kakeibo.ollama import OllamaAnalyser
from mizu_kakeibo.scheduled import MALAYSIA, PowerState, WakeState

FULL_WAKE = WakeState(lid_closed=False, capabilities=["CPU", "Graphics", "Audio", "Network"])
SUBJECT = "Fictional subject that must never be recorded"


class FakeSystem:
    def __init__(self) -> None:
        self.wake = FULL_WAKE
        self.power = PowerState("ac", None)
        self.versions: list[str | None] = ["0.40.5"]
        self.started: list[str] = []
        self.sleeps = 0
        self.kept_awake = 0

    def wake_state(self):
        return self.wake

    def power_state(self):
        return self.power

    def runtime_version(self):
        return self.versions.pop(0) if len(self.versions) > 1 else self.versions[0]

    def start_runtime(self):
        self.started.append("kickstart")
        return "kickstart"

    @contextmanager
    def keep_awake(self):
        self.kept_awake += 1
        yield

    def sleep(self, _seconds):
        self.sleeps += 1


class Clock:
    def __init__(self) -> None:
        self.time = datetime(2026, 10, 17, 10, 0, 4, tzinfo=MALAYSIA)

    def __call__(self) -> datetime:
        return self.time


@pytest.fixture
def run(tmp_path, monkeypatch):
    directory = tmp_path / "gmail-api"
    write_token(directory)
    live = tmp_path / "live"
    live.mkdir()
    analyser = FakeAnalyser()
    fake = FakeGmail(
        [
            api_message("m1", "Example Store <receipts@example.invalid>", "Receipt"),
            api_message(
                "m2", "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "Jobs", offset=1
            ),
            api_message("m3", "Example Friend <friend@example.invalid>", SUBJECT, offset=2),
        ]
    )
    system = FakeSystem()
    clock = Clock()
    monkeypatch.setattr(cli, "OllamaAnalyser", lambda: analyser)
    monkeypatch.setattr(
        cli, "GmailApiClient", lambda creds: gmail_api.GmailApiClient(creds, client=fake.client())
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
    ]

    def records():
        runs = live / "runs"
        if not runs.is_dir():
            return []
        return [
            json.loads((path / "run.json").read_text())
            for path in sorted(runs.iterdir())
            if (path / "run.json").is_file()
        ]

    def log():
        path = live / "scheduled.log"
        return path.read_text().splitlines() if path.exists() else []

    return SimpleNamespace(
        argv=argv,
        live=live,
        fake=fake,
        analyser=analyser,
        system=system,
        clock=clock,
        directory=directory,
        records=records,
        log=log,
        main=lambda: cli.main(argv),
    )


def write_record(live, started: datetime, **record) -> None:
    directory = live / "runs" / started.strftime(scheduled.RUN_NAME_FORMAT)
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps(record))


def test_a_scheduled_sync_writes_its_record_and_one_log_line(run, capsys) -> None:
    assert run.main() == 0
    [record] = run.records()
    assert record["trigger"] == "command"
    assert record["started_at"] == "2026-10-17T10:00:04+08:00"
    assert record["outcome"] == "synced"
    assert record["runtime_version"] == "0.40.5"
    assert record["full_search"] is False
    assert record["groups"] == {
        "expense": ["m1"],
        "not_expense": ["m3"],
        "needs_review": [],
        "skipped": [],
        "filtered": ["m2"],
        "failed": [],
        "deferred": [],
    }
    assert record["counts"]["expense"] == 1
    assert record["counts"]["filtered"] == 1
    assert record["wake"] == {
        "lid_closed": False,
        "capabilities": ["CPU", "Graphics", "Audio", "Network"],
        "power_source": "ac",
        "battery_percent": None,
    }
    assert record["day_done"] is True
    assert run.system.kept_awake == 1
    assert run.system.started == []
    [line] = run.log()
    assert line == (
        "2026-10-17T10:00:04+08:00 trigger=command outcome=synced run=20261017-100004 "
        "expense=1 not_expense=1 needs_review=0 skipped=0 filtered=1 failed=0 deferred=0 "
        "day_done=yes"
    )
    assert capsys.readouterr().out == line + "\n"
    run_directory = run.live / "runs" / "20261017-100004"
    assert run_directory.stat().st_mode & 0o777 == 0o700
    assert (run_directory / "run.json").stat().st_mode & 0o777 == 0o600
    assert (run.live / "scheduled.log").stat().st_mode & 0o777 == 0o600


def test_launchd_is_recorded_as_the_trigger(run, monkeypatch) -> None:
    monkeypatch.setenv("XPC_SERVICE_NAME", scheduled.SYNC_LABEL)
    assert run.main() == 0
    assert run.records()[0]["trigger"] == "launchd"


def test_a_run_that_finds_the_lock_held_logs_it_and_records_nothing(run, hold_run_lock) -> None:
    holder = hold_run_lock(run.live / "kakeibo.sqlite3")
    assert run.main() == 0
    assert run.log() == [
        f"2026-10-17T10:00:04+08:00 trigger=command outcome=locked holder_pid={holder.pid}"
    ]
    assert run.records() == []
    assert run.fake.requests == []
    assert run.system.kept_awake == 0


def test_a_done_day_is_not_run_again_but_the_next_day_is(run) -> None:
    assert run.main() == 0
    run.fake.requests.clear()
    run.clock.time += timedelta(hours=1)
    assert run.main() == 0
    assert run.log()[-1] == "2026-10-17T11:00:04+08:00 trigger=command outcome=done_today"
    assert run.fake.requests == []
    assert len(run.records()) == 1

    run.clock.time = datetime(2026, 10, 18, 8, 0, 4, tzinfo=MALAYSIA)
    assert run.main() == 0
    assert run.fake.requests != []
    assert len(run.records()) == 2


def test_a_manual_sync_does_not_mark_the_day_done(run, capsys) -> None:
    manual = ["sync", *run.argv[1:]]
    assert cli.main(manual) == 0
    capsys.readouterr()
    assert run.main() == 0
    [record] = run.records()
    assert record["groups"]["skipped"] == ["m1", "m3"]


@pytest.mark.parametrize(
    ("wake", "logged"),
    [
        (WakeState(True, ["CPU", "Graphics"]), "lid_closed=yes graphics=yes"),
        (WakeState(False, ["CPU", "Network"]), "lid_closed=no graphics=no"),
        (WakeState(None, None), "lid_closed=unknown graphics=unknown"),
    ],
)
def test_a_maintenance_wake_or_closed_lid_skips_until_the_next_full_wake(run, wake, logged) -> None:
    run.system.wake = wake
    assert run.main() == 0
    assert run.log() == [
        f"2026-10-17T10:00:04+08:00 trigger=command outcome=not_full_wake {logged}"
    ]
    assert run.fake.requests == []
    assert run.records() == []
    assert run.system.kept_awake == 0

    run.system.wake = FULL_WAKE
    run.clock.time += timedelta(minutes=40)
    assert run.main() == 0
    assert run.records()[0]["day_done"] is True


@pytest.mark.parametrize(
    ("power", "logged"),
    [
        (PowerState("battery", 19), "power=battery battery=19%"),
        (PowerState("battery", None), "power=battery battery=unknown"),
        (PowerState(None, None), "power=unknown battery=unknown"),
    ],
)
def test_low_or_unknown_power_skips_the_run(run, power, logged) -> None:
    run.system.power = power
    assert run.main() == 0
    assert run.log() == [f"2026-10-17T10:00:04+08:00 trigger=command outcome=low_power {logged}"]
    assert run.fake.requests == []


def test_a_battery_at_the_minimum_charge_runs(run) -> None:
    run.system.power = PowerState("battery", 20)
    assert run.main() == 0
    assert run.records()[0]["wake"]["battery_percent"] == 20


def test_a_stopped_runtime_is_started_before_retrieval(run) -> None:
    run.system.versions = [None, None, None, "0.40.5"]
    assert run.main() == 0
    assert run.system.started == ["kickstart"]
    assert run.system.sleeps == 3
    record = run.records()[0]
    assert record["runtime_started_with"] == "kickstart"
    assert record["day_done"] is True


def test_a_runtime_that_never_answers_fails_before_retrieval(run) -> None:
    run.system.versions = [None]
    assert run.main() == 1
    assert run.system.sleeps == scheduled.RUNTIME_START_SECONDS
    assert run.fake.requests == []
    [record] = run.records()
    assert record["outcome"] == "failed"
    assert record["failed_stage"] == "runtime"
    assert record["groups"] is None
    assert record["counts"] is None
    assert record["day_done"] is False
    assert "did not answer within 30 seconds" in record["error"]
    assert run.log()[-1].endswith("outcome=failed run=20261017-100004 stage=runtime")


def test_a_wrong_runtime_version_fails_before_retrieval(run, monkeypatch) -> None:
    def ollama(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.41.0"})
        return httpx.Response(200, json={"models": [{"name": MODEL, "digest": MODEL_DIGEST}]})

    monkeypatch.setattr(
        cli,
        "OllamaAnalyser",
        lambda: OllamaAnalyser(
            client=httpx.Client(base_url=OLLAMA_BASE_URL, transport=httpx.MockTransport(ollama))
        ),
    )
    run.system.versions = ["0.41.0"]
    assert run.main() == 1
    assert run.fake.requests == []
    [record] = run.records()
    assert record["failed_stage"] == "runtime"
    assert record["runtime_version"] == "0.41.0"
    assert "Ollama version mismatch" in record["error"]


def test_an_expired_sign_in_fails_before_the_runtime_and_retrieval(run, monkeypatch) -> None:
    write_token(run.directory, expiry="2026-01-01T00:00:00Z")
    endpoint = FakeTokenEndpoint(400, {"error": "invalid_grant"})
    monkeypatch.setattr(gmail_api, "_refresh_request", lambda: endpoint)
    run.system.versions = [None]
    assert run.main() == 1
    assert run.system.started == []
    assert run.fake.requests == []
    [record] = run.records()
    assert record["failed_stage"] == "sign_in"
    assert "gmail-sign-in" in record["error"]


def test_a_failed_day_is_retried_with_a_full_search_in_the_next_hour(run) -> None:
    run.fake.raw_errors["m1"] = 429
    assert run.main() == 1
    first = run.records()[0]
    assert first["groups"]["failed"] == ["m1"]
    assert first["message_errors"] == {
        "m1": "Gmail retrieval error: Gmail API returned HTTP 429 rateLimitExceeded"
    }
    assert first["day_done"] is False

    # A run that fails before processing does not lose the need for a full search.
    run.clock.time += timedelta(hours=1)
    run.fake.status = 503
    assert run.main() == 1
    assert run.records()[1]["failed_stage"] == "retrieval"

    run.fake.status = None
    run.fake.raw_errors.clear()
    run.fake.requests.clear()
    run.clock.time += timedelta(hours=1)
    assert run.main() == 0
    third = run.records()[2]
    assert third["full_search"] is True
    assert third["groups"]["expense"] == ["m1"]
    assert third["day_done"] is True
    search = next(r for r in run.fake.requests if r.url.path.endswith("/messages"))
    assert search.url.params["q"].endswith("after:2026/10/15")

    run.clock.time += timedelta(hours=1)
    assert run.main() == 0
    assert run.log()[-1].endswith("outcome=done_today")


def test_a_deferred_day_is_continued_in_the_next_hour(run, monkeypatch) -> None:
    monkeypatch.setattr(cli, "SYNC_BATCH_LIMIT", 1)
    assert run.main() == 0
    first = run.records()[0]
    assert first["groups"]["deferred"] == ["m3"]
    assert first["day_done"] is False
    assert run.log()[-1].endswith("deferred=1 day_done=no")

    run.clock.time += timedelta(hours=1)
    assert run.main() == 0
    second = run.records()[1]
    assert second["full_search"] is False
    assert second["groups"]["not_expense"] == ["m3"]
    assert second["day_done"] is True


def test_records_and_log_lines_older_than_90_days_are_deleted(run) -> None:
    old = run.clock.time - timedelta(days=91)
    recent = run.clock.time - timedelta(days=89)
    write_record(run.live, old, day_done=True)
    write_record(run.live, recent, day_done=True)
    manual = run.live / "runs" / "20260701-120000"  # an agent-driven sync's directory
    manual.mkdir()
    (manual / "result.json").write_text("{}")
    (run.live / "scheduled.log").write_text(
        f"{old.isoformat(timespec='seconds')} trigger=launchd outcome=done_today\n"
        f"{recent.isoformat(timespec='seconds')} trigger=launchd outcome=done_today\n"
    )
    run.system.wake = WakeState(True, ["CPU"])

    assert run.main() == 0  # a gate stop still cleans up
    remaining = sorted(path.name for path in (run.live / "runs").iterdir())
    assert remaining == sorted([manual.name, recent.strftime(scheduled.RUN_NAME_FORMAT)])
    log = run.log()
    assert len(log) == 2
    assert log[0].startswith(recent.isoformat(timespec="seconds"))
    assert log[1].endswith("outcome=not_full_wake lid_closed=yes graphics=no")
    assert (run.live / "scheduled.log").stat().st_mode & 0o777 == 0o600


def test_records_and_log_hold_no_message_content(run, capsys) -> None:
    run.fake.messages["m1"]["raw"] = "A"  # cannot be normalized
    assert run.main() == 1
    output = capsys.readouterr().out
    [record] = run.records()
    assert record["message_errors"] == {"m1": "normalization failed"}
    written = output + "".join(
        path.read_text() for path in run.live.rglob("*") if path.suffix in {".json", ".log"}
    )
    for private in (SNIPPET, SUBJECT, "Example Store", "receipts@example.invalid", "MYR 42.50"):
        assert private not in written


def test_an_unexpected_error_is_recorded_by_type_and_raised(run, monkeypatch) -> None:
    def broken(*_, **__):
        raise KeyError(SUBJECT)

    monkeypatch.setattr(cli, "run_sync", broken)
    with pytest.raises(KeyError):
        run.main()
    [record] = run.records()
    assert record["failed_stage"] == "unexpected"
    assert record["error"] == "KeyError"
    assert run.log()[-1].endswith("stage=unexpected")


def test_the_launch_agent_runs_the_installed_command_every_hour(tmp_path, capsysbinary) -> None:
    program = tmp_path / "bin" / "mizu-kakeibo"
    program.parent.mkdir()
    program.write_text("")
    live = tmp_path / "live"
    assert (
        cli.main(
            [
                "launch-agent",
                "--program",
                str(program),
                "--database",
                str(live / "kakeibo.sqlite3"),
                "--ledger",
                str(live / "ledger.csv"),
                "--gmail-directory",
                str(tmp_path / "gmail-api"),
            ]
        )
        == 0
    )
    agent = plistlib.loads(capsysbinary.readouterr().out)
    assert agent["Label"] == "com.mizu-kakeibo.sync"
    assert agent["ProgramArguments"] == [
        str(program),
        "scheduled-sync",
        "--database",
        str(live / "kakeibo.sqlite3"),
        "--ledger",
        str(live / "ledger.csv"),
        "--gmail-directory",
        str(tmp_path / "gmail-api"),
    ]
    assert agent["StartCalendarInterval"] == [{"Hour": hour, "Minute": 0} for hour in range(8, 24)]
    assert agent["ProcessType"] == "Background"
    assert "RunAtLoad" not in agent
    assert "KeepAlive" not in agent
    assert agent["StandardOutPath"] == str(live / "launchd.out.log")
    assert agent["StandardErrorPath"] == str(live / "launchd.err.log")


def test_the_launch_agent_needs_the_installed_command(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit):
        cli.main(
            [
                "launch-agent",
                "--program",
                str(tmp_path / "missing"),
                "--database",
                str(tmp_path / "kakeibo.sqlite3"),
                "--ledger",
                str(tmp_path / "ledger.csv"),
            ]
        )
    assert "uv tool install" in capsys.readouterr().err


def test_gate_states_are_read_from_the_macos_commands() -> None:
    assert scheduled.parse_clamshell('      "AppleClamshellState" = No\n') is False
    assert scheduled.parse_clamshell('      "AppleClamshellState" = Yes\n') is True
    assert scheduled.parse_clamshell("") is None
    assert scheduled.parse_capabilities(
        "Current System Capabilities are: CPU Graphics Audio Network \nCurrent Power State: 4\n"
    ) == ["CPU", "Graphics", "Audio", "Network"]
    assert scheduled.parse_capabilities("Current System Capabilities are: CPU Network\n") == [
        "CPU",
        "Network",
    ]
    assert scheduled.parse_capabilities("") is None
    assert scheduled.parse_power(
        "Now drawing from 'Battery Power'\n"
        " -InternalBattery-0 (id=1234)\t56%; discharging; 3:28 remaining present: true\n"
    ) == PowerState("battery", 56)
    assert scheduled.parse_power(
        "Now drawing from 'AC Power'\n"
        " -InternalBattery-0 (id=1234)\t100%; charged; 0:00 remaining present: true\n"
    ) == PowerState("ac", 100)
    assert scheduled.parse_power("") == PowerState(None, None)


@pytest.mark.parametrize(
    ("loaded", "expected"),
    [
        (True, ["kickstart", "gui/501/com.mizu-kakeibo.ollama"]),
        (False, ["bootstrap", "gui/501", str(scheduled.OLLAMA_AGENT)]),
    ],
)
def test_the_runtime_is_kickstarted_when_loaded_and_bootstrapped_otherwise(
    monkeypatch, loaded, expected
) -> None:
    calls = []

    def launchctl(command, **_):
        calls.append(command[1:])
        status = 0 if command[1] != "print" or loaded else 113
        return SimpleNamespace(returncode=status)

    monkeypatch.setattr(scheduled.os, "getuid", lambda: 501)
    monkeypatch.setattr(scheduled.subprocess, "run", launchctl)
    assert scheduled.System().start_runtime() == expected[0]
    assert calls == [["print", "gui/501/com.mizu-kakeibo.ollama"], expected]


def test_a_failed_runtime_start_is_a_runtime_error(monkeypatch) -> None:
    monkeypatch.setattr(
        scheduled.subprocess, "run", lambda command, **_: SimpleNamespace(returncode=5)
    )
    with pytest.raises(scheduled.AnalysisError, match="launchctl bootstrap exited 5"):
        scheduled.System().start_runtime()
