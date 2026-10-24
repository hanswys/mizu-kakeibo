from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from jobs_fakes import FakeConsole, ScriptedOllama, message, other, update

from mizu_kakeibo import cli
from mizu_kakeibo.jobs.processor import JobProcessor
from mizu_kakeibo.jobs.skim import skim, skim_list
from mizu_kakeibo.jobs.storage import JobStore
from mizu_kakeibo.spending import parse_period

LINKEDIN = "LinkedIn <jobs-noreply@linkedin.com>"
WORKABLE = "Example Hiring <no-reply@apply.workable.com>"
# Hexadecimal IDs, as Gmail's are, so a listed email can be opened; `9z` has no Gmail link.
ALERT = message("c1", 1, "Analyst roles for you", sender=LINKEDIN)
TIPS = message("c2", 4, "Interview tips for this week")
# 20:00 UTC on Sunday 3 May is 04:00 on Monday 4 May in Malaysia, so it falls in that week.
LATE = message("9z", 3, "Hiring update", sender=WORKABLE).model_copy(
    update={"received_at": datetime(2026, 5, 3, 20, 0, tzinfo=timezone.utc)}
)
READING = message("c3", 5, "Weekly reading list")
UNCLEAR = message("c4", 6, "Thanks for applying")
ORCHID = message("c5", 7, "Orchid Harbour received your Product Designer application")
DIGEST = message("c6", 12, "Your applications digest")
ANSWERS = {
    ALERT.subject: other("not_application"),
    TIPS.subject: other("not_application"),
    LATE.subject: other("not_application"),
    READING.subject: other("not_application"),
    UNCLEAR.subject: other("needs_review", ["company"], "The company is missing"),
    ORCHID.subject: update("Orchid Harbour", "Product Designer", "applied"),
    DIGEST.subject: other("not_application"),
}
TODAY = date(2026, 5, 6)


def build(database):
    store = JobStore(database)
    with ScriptedOllama(ANSWERS).analyser() as analyser:
        JobProcessor(store, analyser).process_batch(
            [ALERT, TIPS, LATE, READING, UNCLEAR, ORCHID, DIGEST]
        )
    return database


@pytest.fixture
def database(tmp_path):
    return build(tmp_path / "job-tracker.sqlite3")


def ids(messages) -> list[str]:
    return [message.message_id for message in messages]


def test_the_list_takes_job_related_not_applications_received_in_the_period(database) -> None:
    week = parse_period("week", TODAY)
    assert ids(skim_list(database, week.start, week.end)) == ["9z", "c2"]
    month = parse_period("2026-05", TODAY)
    assert ids(skim_list(database, month.start, month.end)) == ["c1", "9z", "c2", "c6"]
    assert skim_list(database, date(2026, 6, 1), date(2026, 6, 30)) == []


def test_jobs_skim_prints_the_week_by_default_and_records_nothing(
    database, monkeypatch, capsys, hold_run_lock
) -> None:
    monkeypatch.setattr(cli, "_today", lambda: TODAY)
    hold_run_lock(database)
    database.chmod(0o400)
    before = database.read_bytes()

    assert cli.main(["jobs-skim", "--database", str(database)]) == 0
    assert capsys.readouterr().out == (
        "This week, Mon 4 May – Sun 10 May 2026\n"
        "\n"
        "  Mon  4 May  Example Hiring   Hiring update\n"
        "  Mon  4 May  Example Careers  Interview tips for this week\n"
        "\n"
        "  2 emails to skim\n"
    )
    assert cli.main(["jobs-skim", "last-month", "--database", str(database)]) == 0
    assert capsys.readouterr().out == "April 2026\n\n  Nothing to skim.\n"
    assert database.read_bytes() == before


def test_in_a_terminal_a_numbered_email_opens_in_gmail(database) -> None:
    console = FakeConsole(["5", "x", "1", "4", "q"])

    assert skim(database, parse_period("2026-05", TODAY), console) == 0

    assert "  1  Fri  1 May  LinkedIn         Analyst roles for you" in console.output[0]
    assert "  4  Tue 12 May  Example Careers  Your applications digest" in console.output[0]
    assert console.prompts == ["Number to open in Gmail, or Enter or q to quit: "] * 5
    assert console.output.count("  Type a number from 1 to 4.") == 2
    assert console.opened == [
        "https://mail.google.com/mail/#all/thread-f:6699|msg-f:193",
        "https://mail.google.com/mail/#all/thread-f:6699|msg-f:198",
    ]


def test_an_email_without_a_gmail_link_says_so_and_leaving_ends_the_list(database) -> None:
    console = FakeConsole(["1", ""])
    assert skim(database, parse_period("week", TODAY), console) == 0
    assert "  Email 1 has no Gmail link." in console.output
    assert console.opened == []

    console = FakeConsole([])
    assert skim(database, parse_period("week", TODAY), console) == 0
    assert len(console.prompts) == 1


def test_an_empty_list_or_no_terminal_asks_nothing(database) -> None:
    console = FakeConsole([])
    skim(database, parse_period("last-month", TODAY), console)
    assert console.prompts == []

    console = FakeConsole([], interactive=False)
    skim(database, parse_period("week", TODAY), console)
    assert console.prompts == []
    assert "  Mon  4 May  Example Hiring   Hiring update" in console.output[0]


def test_jobs_skim_rejects_an_unknown_period(database, capsys) -> None:
    with pytest.raises(SystemExit) as exit:
        cli.main(["jobs-skim", "fortnight", "--database", str(database)])
    assert exit.value.code == 2
    assert "period must be one of" in capsys.readouterr().err


def test_status_counts_this_weeks_emails_to_skim(private_live_paths, monkeypatch, capsys) -> None:
    cli.Store(private_live_paths / "kakeibo.sqlite3", private_live_paths / "ledger.csv")
    build(cli.JOBS_LIVE_DATABASE)

    monkeypatch.setattr(cli, "_today", lambda: TODAY)
    assert cli.main([]) == 0
    output = capsys.readouterr().out
    assert "  Jobs        1 application · 1 to review · 2 to skim this week\n" in output
    assert "  mizu-kakeibo jobs-skim " in output

    # Only this week counts, so a later week has nothing to skim.
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 5, 18))
    assert cli.main([]) == 0
    assert "  Jobs        1 application · 1 to review\n" in capsys.readouterr().out
