from __future__ import annotations

import hashlib

import pytest
from jobs_fakes import FakeConsole, ScriptedOllama, message, update

from mizu_kakeibo import cli
from mizu_kakeibo.jobs.history import find_history, show
from mizu_kakeibo.jobs.models import JobStatus
from mizu_kakeibo.jobs.processor import JobProcessor
from mizu_kakeibo.jobs.storage import JobStore, ResolutionAction, read_counts

# Each application's first email, in received order, so application IDs follow this order.
# Hexadecimal IDs, as Gmail's are, so a listed email can be opened.
UPDATES = [
    ("b1", 1, "Alder Labs", "Data Analyst", "applied"),
    ("b2", 1, "Fern Studio", "UX Researcher", "interview"),
    ("b3", 2, "Birch Logistics", "Warehouse Planner", "applied"),
    ("b4", 2, "Cedar Health", "Nurse Educator", "applied"),
    ("b5", 3, "Ivy Robotics", "Firmware Engineer", "applied"),
    ("b6", 3, "Dune Media", "Video Editor", "viewed"),
    ("b7", 4, "Elm Finance", "Credit Officer", "interview"),
    ("b8", 5, "Grove Energy", "Site Engineer", "offered"),
    ("b9", 6, "Heath Retail", "Store Manager", "rejected"),
    ("ba", 7, "Alder Labs", "Data Analyst", "interview"),
    # A likely match of Alder Labs, attached to it through review.
    ("bb", 8, "Alder Labs Group", "Data Analyst", "interview"),
]


def subject(company: str, job_title: str, status: str) -> str:
    return f"{company}: {job_title} {status}"


def process(store: JobStore, updates) -> None:
    messages = [
        message(message_id, day, subject(company, job_title, status))
        for message_id, day, company, job_title, status in updates
    ]
    answers = {
        subject(company, job_title, status): update(company, job_title, status)
        for _, _, company, job_title, status in updates
    }
    with ScriptedOllama(answers).analyser() as analyser:
        JobProcessor(store, analyser).process_batch(messages)


@pytest.fixture
def database(tmp_path):
    store = JobStore(tmp_path / "job-tracker.sqlite3")
    process(store, UPDATES)
    [item] = store.list_review_items()
    store.resolve_review(
        item.id,
        ResolutionAction.ATTACH,
        application_id=1,
        company="Alder Labs Group",
        job_title="Data Analyst",
        status=JobStatus.INTERVIEW,
    )
    return store.database_path


def files(database) -> dict[str, str]:
    """Every file beside the database, with a hash of its contents."""
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(database.parent.iterdir())
    }


def opened_read_only(database) -> dict[str, str]:
    """Open the database read-only once, as `status` does, then return `files`.

    SQLite's first read-only open of a WAL database it cannot checkpoint leaves an empty `-wal`
    and a `-shm` beside it, and later reads change neither, so the files are compared after it.
    """
    before = database.read_bytes()
    read_counts(database)
    assert database.read_bytes() == before
    return files(database)


def test_the_list_orders_by_status_then_latest_update_then_id(database, capsys) -> None:
    assert cli.main(["jobs-list", "--database", str(database)]) == 0

    assert capsys.readouterr().out == (
        "9 applications\n"
        "\n"
        "     Updated     Status     Company          Job title\n"
        "  1  2026-05-05  offered    Grove Energy     Site Engineer\n"
        "  2  2026-05-08  interview  Alder Labs       Data Analyst\n"
        "  3  2026-05-04  interview  Elm Finance      Credit Officer\n"
        "  4  2026-05-01  interview  Fern Studio      UX Researcher\n"
        "  5  2026-05-03  viewed     Dune Media       Video Editor\n"
        "  6  2026-05-03  applied    Ivy Robotics     Firmware Engineer\n"
        "  7  2026-05-02  applied    Birch Logistics  Warehouse Planner\n"
        "  8  2026-05-02  applied    Cedar Health     Nurse Educator\n"
        "  9  2026-05-06  rejected   Heath Retail     Store Manager\n"
    )


def test_a_number_changes_when_an_application_changes_status(database) -> None:
    assert find_history(database, "8").application.company == "Cedar Health"

    process(JobStore(database), [("bc", 9, "Cedar Health", "Nurse Educator", "interview")])

    assert find_history(database, "2").application.company == "Cedar Health"
    assert find_history(database, "8").application.company == "Birch Logistics"


def test_jobs_show_prints_the_application_and_its_emails_and_changes_no_file(
    database, capsys, monkeypatch, hold_run_lock
) -> None:
    monkeypatch.setenv("COLUMNS", "120")
    hold_run_lock(database)
    before = opened_read_only(database)

    assert cli.main(["jobs-show", "2", "--database", str(database)]) == 0

    assert capsys.readouterr().out == (
        "  Company    Alder Labs\n"
        "  Job title  Data Analyst\n"
        "  Status     interview, updated 2026-05-08\n"
        "  Aliases    Alder Labs Group · Data Analyst\n"
        "\n"
        "  Fri  1 May  Example Careers  applied    created               "
        "Alder Labs: Data Analyst applied\n"
        "  Thu  7 May  Example Careers  interview  advanced              "
        "Alder Labs: Data Analyst interview\n"
        "  Fri  8 May  Example Careers  interview  unchanged (reviewed)  "
        "Alder Labs Group: Data Analyst interview\n"
        "\n"
        "  3 emails\n"
    )
    assert cli.main(["jobs-show", "1", "--database", str(database)]) == 0
    assert "  Aliases    none\n" in capsys.readouterr().out
    assert cli.main(["jobs-list", "--database", str(database)]) == 0
    assert files(database) == before


def test_jobs_show_works_on_a_database_it_cannot_write(database, capsys) -> None:
    database.chmod(0o400)
    try:
        assert cli.main(["jobs-show", "9", "--database", str(database)]) == 0
        assert "  Company    Heath Retail\n" in capsys.readouterr().out
    finally:
        database.chmod(0o600)


@pytest.mark.parametrize("number", ["0", "10", "two", "-1", ""])
def test_an_unknown_number_prints_an_error_and_exits_1(database, capsys, number) -> None:
    before = opened_read_only(database)

    assert cli.main(["jobs-show", number, "--database", str(database)]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.startswith("mizu-kakeibo jobs-show: no application ")
    assert "run mizu-kakeibo jobs-list for the numbers" in output.err
    assert files(database) == before


def test_in_a_terminal_a_numbered_email_opens_in_gmail(database) -> None:
    console = FakeConsole(["4", "x", "1", "3", ""])

    assert show(find_history(database, "2"), console) == 0

    assert "  1  Fri  1 May  Example Careers  applied" in console.output[0]
    assert "  3  Fri  8 May  Example Careers  interview  unchanged (reviewed)" in console.output[0]
    assert console.prompts == ["Number to open in Gmail, or Enter or q to quit: "] * 5
    assert console.output.count("  Type a number from 1 to 3.") == 2
    assert console.opened == [
        "https://mail.google.com/mail/#all/thread-f:6699|msg-f:177",
        "https://mail.google.com/mail/#all/thread-f:6699|msg-f:187",
    ]


def test_without_a_terminal_it_prints_the_emails_unnumbered_and_asks_nothing(database) -> None:
    console = FakeConsole([], interactive=False)

    assert show(find_history(database, "2"), console) == 0

    assert console.prompts == []
    assert "\n  Fri  1 May  Example Careers  applied" in console.output[0]
