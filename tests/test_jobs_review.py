from __future__ import annotations

import json

import pytest
from jobs_dataset import BOARD, BOARD_FOOTER, raw_message
from jobs_fakes import FakeConsole, ScriptedOllama, message, other, update

from mizu_kakeibo import cli
from mizu_kakeibo.jobs.models import JobStatus
from mizu_kakeibo.jobs.processor import JobProcessor
from mizu_kakeibo.jobs.review import application_list, parse_status, review
from mizu_kakeibo.jobs.storage import JobStore

# Hexadecimal IDs, as Gmail's are, so the review can build a Gmail link.
ORCHID = message("a1", 1, "Orchid Harbour received your Product Designer application")
LUMEN = message("a2", 2, "Lumen Works: Backend Engineer received")
GROUP = message("a3", 3, "Orchid Harbour Group: interview for Product Designer")
UNCLEAR = message(
    "a4",
    4,
    "Thanks for applying",
    "\n".join(f"Fictional line {number}" for number in range(1, 21)),
)
ANSWERS = {
    ORCHID.subject: update("Orchid Harbour", "Product Designer", "applied"),
    LUMEN.subject: update("Lumen Works", "Backend Engineer", "applied"),
    GROUP.subject: update("Orchid Harbour Group", "Product Designer", "interview"),
    UNCLEAR.subject: other("needs_review", ["company", "job_title"], "Names are missing"),
}


@pytest.fixture
def store(tmp_path) -> JobStore:
    store = JobStore(tmp_path / "job-tracker.sqlite3")
    with ScriptedOllama(ANSWERS).analyser() as analyser:
        JobProcessor(store, analyser).process_batch([ORCHID, LUMEN, GROUP, UNCLEAR])
    return store


def statuses(store):
    return {
        (application.company, application.job_title): application.status
        for application in store.list_applications()
    }


def test_the_list_and_an_item_show_the_email_and_the_suggestion(store) -> None:
    console = FakeConsole(["1", "m", "g", "s", "q"])

    assert review(store, console) == 0

    text = console.text
    assert "2 messages to review" in text
    assert "Sun  3 May  Example Careers" in text
    assert "likely match" in text and "missing company, job title" in text
    assert "Extracted  Orchid Harbour Group · Product Designer · interview" in text
    assert "Suggested  #1 Orchid Harbour · Product Designer · applied, updated 2026-05-01" in text
    assert console.paged == ["An update about: " + GROUP.subject + "\n"]
    assert console.opened == ["https://mail.google.com/mail/#all/thread-f:6699|msg-f:163"]
    assert console.prompts[1].startswith("a accept · e edit")
    assert len(store.list_review_items()) == 2


def test_accepting_a_likely_match_attaches_it_and_saves_the_other_name(store) -> None:
    console = FakeConsole(["1", "a", "y", "s"])

    review(store, console)

    assert "Orchid Harbour Group · Product Designer becomes another name for it" in console.text
    assert "Saved." in console.text
    assert statuses(store)[("Orchid Harbour", "Product Designer")] is JobStatus.INTERVIEW
    assert len(store.list_review_items()) == 1


def test_editing_values_matches_them_and_explains_a_likely_match(store) -> None:
    # The second item has no extracted values, so accept is not offered and values are typed.
    console = FakeConsole(
        [
            "2",
            "e",
            "Orchid Harbour Group",
            "Product Designer",
            "x",
            "rej",
            "e",
            "LUMEN WORKS SDN BHD",
            "Backend Engineer",
            "v",
            "y",
        ]
    )

    review(store, console)

    assert not console.prompts[1].startswith("a accept")
    assert "Type applied, viewed, interview, rejected, or offered." in console.text
    assert "This looks like application #1 Orchid Harbour" in console.text
    assert "Attach to #2 Lumen Works · Backend Engineer · applied" in console.text
    assert statuses(store)[("Lumen Works", "Backend Engineer")] is JobStatus.VIEWED
    assert [item.message_id for item in store.list_review_items()] == ["a3"]


def test_attaching_to_another_application_picks_it_by_number(store) -> None:
    console = FakeConsole(["2", "t", "9", "#2", "", "", "i", "y"])

    review(store, console)

    assert "Type the number of an application in the list." in console.text
    assert "becomes another name" not in console.text
    assert statuses(store)[("Lumen Works", "Backend Engineer")] is JobStatus.INTERVIEW


def test_attaching_never_offers_a_placeholder_name(store) -> None:
    # The guard replaced this output because its job title is a placeholder.
    guarded = message("a5", 5, "Lumen Works: your application was viewed")
    with ScriptedOllama({guarded.subject: update("Lumen Works", "null", "viewed")}).analyser() as (
        analyser
    ):
        JobProcessor(store, analyser).process_batch([guarded])
    console = FakeConsole(["3", "t", "2", "", "", "", "y"])

    review(store, console)

    assert "Extracted  Lumen Works · ? · viewed" in console.text
    assert "Job title [Backend Engineer]: " in console.prompts
    assert "null" not in "".join(console.prompts)
    assert "becomes another name" not in console.text
    assert statuses(store)[("Lumen Works", "Backend Engineer")] is JobStatus.VIEWED
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM aliases").fetchone()[0] == 0


def test_a_new_application_is_refused_for_names_that_exist(store) -> None:
    console = FakeConsole(
        [
            "2",
            "c",
            "Lumen Works",
            "Backend Engineer",
            "a",
            "c",
            "Pinecrest Foods",
            "Buyer",
            "a",
            "y",
        ]
    )

    review(store, console)

    assert "That is already application #2" in console.text
    assert statuses(store)[("Pinecrest Foods", "Buyer")] is JobStatus.APPLIED


def test_not_an_application_and_leaving_save_nothing_else(store) -> None:
    review(store, FakeConsole(["1", "e", "q"]))
    assert len(store.list_review_items()) == 2

    review(store, FakeConsole(["2", "n", "n", "n", "y"]))

    assert [item.message_id for item in store.list_review_items()] == ["a3"]
    assert len(store.list_applications()) == 2


def test_message_text_cannot_drive_the_terminal(tmp_path) -> None:
    store = JobStore(tmp_path / "job-tracker.sqlite3")
    hostile = message("b1", 1, "Hello\x1b[2J there", "Line\x1b]0;title\x07 one‮")
    with ScriptedOllama(
        {hostile.subject: other("needs_review", ["status"])}
    ).analyser() as analyser:
        JobProcessor(store, analyser).process_one(hostile)
    console = FakeConsole(["1", "s", "q"])

    review(store, console)

    assert "\x1b" not in console.text and "‮" not in console.text


def test_without_a_terminal_it_prints_the_list_and_asks_nothing(store) -> None:
    console = FakeConsole([], interactive=False)

    review(store, console)

    assert "2 messages to review" in console.text
    assert console.prompts == []


def test_statuses_may_be_typed_by_their_first_letters() -> None:
    assert parse_status(" Offered ") is JobStatus.OFFERED
    assert parse_status("i") is JobStatus.INTERVIEW
    with pytest.raises(ValueError):
        parse_status("")


def test_the_application_list_shows_newest_updates_first(store) -> None:
    text = application_list(store, 100)

    assert text.splitlines()[:5] == [
        "2 applications",
        "",
        "      Updated     Status     Company         Job title",
        "  #2  2026-05-02  applied    Lumen Works     Backend Engineer",
        "  #1  2026-05-01  applied    Orchid Harbour  Product Designer",
    ]
    assert text.endswith("2 messages to review: run mizu-kakeibo jobs-review\n")


def test_jobs_process_reads_raw_messages_preferring_html_and_reports_the_run(
    tmp_path, monkeypatch, capsys
) -> None:
    # The job title is only in the HTML alternative, so grounding passes only when HTML is used.
    viewed = raw_message(
        "c1",
        1,
        BOARD,
        "Your application was viewed by Kestrel Analytics",
        f"Your application was viewed by Kestrel Analytics\n\n{BOARD_FOOTER}",
        "<h1>Your application was viewed by Kestrel Analytics</h1>"
        f"<p>Data Analyst</p><p>{BOARD_FOOTER}</p>",
    )
    alert = raw_message("c2", 2, BOARD, "Applications you might like", "Jobs for you.")
    broken = {"id": "c3", "raw": "not base64 ☃"}
    source = tmp_path / "messages.json"
    source.write_text(json.dumps([viewed, alert, broken]), encoding="utf-8")
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps({"already_processed_ids": ["c0"], "deferred_ids": ["c9"]}), encoding="utf-8"
    )
    ollama = ScriptedOllama(
        {
            "Your application was viewed by Kestrel Analytics": update(
                "Kestrel Analytics", "Data Analyst", "viewed"
            ),
            "Applications you might like": other("not_application"),
        }
    )
    monkeypatch.setattr(cli, "OllamaJobAnalyser", ollama.analyser)

    assert cli.main(["jobs-process", str(source), "--selection", str(selection)]) == 1

    report = json.loads(capsys.readouterr().out)
    assert report["summary"] == {
        "status_update": ["c1"],
        "not_application": ["c2"],
        "review": [],
        "skipped": ["c0"],
        "failed": ["c3"],
        "deferred": ["c9"],
    }
    assert report["applications"]["created"] == 1
    assert [item["message_id"] for item in report["skim"]] == ["c2"]
    assert cli.JOBS_LIVE_DATABASE.is_file()
    assert not cli.LIVE_DATABASE.exists()

    assert cli.main(["jobs-list"]) == 0
    assert "Kestrel Analytics  Data Analyst" in capsys.readouterr().out
    assert cli.main(["jobs-review"]) == 0
    assert capsys.readouterr().out == "Nothing to review.\n"


def test_jobs_process_records_nothing_while_another_run_holds_its_lock(
    tmp_path, monkeypatch, capsys, hold_run_lock
) -> None:
    source = tmp_path / "messages.json"
    source.write_text(json.dumps(LUMEN.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setattr(cli, "OllamaJobAnalyser", ScriptedOllama(ANSWERS).analyser)
    database = tmp_path / "jobs" / "job-tracker.sqlite3"
    holder = hold_run_lock(database)

    assert cli.main(["jobs-process", str(source), "--database", str(database)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["status"], report["holder"]["pid"]) == ("locked", holder.pid)
    assert not database.exists()


def test_the_expense_lock_does_not_block_a_job_run(tmp_path, monkeypatch, capsys, hold_run_lock):
    source = tmp_path / "messages.json"
    source.write_text(json.dumps(LUMEN.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setattr(cli, "OllamaJobAnalyser", ScriptedOllama(ANSWERS).analyser)
    hold_run_lock(cli.LIVE_DATABASE)

    assert cli.main(["jobs-process", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["status_update"] == ["a2"]


@pytest.mark.parametrize("command", ["jobs-list", "jobs-review", "jobs-skim"])
def test_reading_commands_need_an_existing_database(command, capsys) -> None:
    with pytest.raises(SystemExit) as exit:
        cli.main([command])
    assert exit.value.code == 2
    assert "no job-tracker database" in capsys.readouterr().err
    assert not cli.JOBS_LIVE_DATABASE.exists()
