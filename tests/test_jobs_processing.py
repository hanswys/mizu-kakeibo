from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest
from jobs_fakes import OVERFLOW, TIMEOUT, ScriptedOllama, message, other, update

from mizu_kakeibo.jobs.constants import (
    GROUNDING_FAILURE_REASON,
    MISSING_VALUE_REASON,
    REPEATED_FAILURE_REASON,
    TOO_LONG_REASON,
)
from mizu_kakeibo.jobs.models import JobStatus
from mizu_kakeibo.jobs.processor import JobProcessor, looks_job_related, run_report
from mizu_kakeibo.jobs.storage import (
    JobReviewError,
    JobStore,
    LikelyMatchError,
    ResolutionAction,
    ReviewItemResolvedError,
)
from mizu_kakeibo.jobs.tracker import ReviewCause
from mizu_kakeibo.processor import ProcessingStatus
from mizu_kakeibo.storage import Store

APPLIED, VIEWED, INTERVIEW, REJECTED, OFFERED = (
    JobStatus.APPLIED,
    JobStatus.VIEWED,
    JobStatus.INTERVIEW,
    JobStatus.REJECTED,
    JobStatus.OFFERED,
)


@pytest.fixture
def store(tmp_path) -> JobStore:
    return JobStore(tmp_path / "jobs" / "job-tracker.sqlite3")


def run(store, answers, *messages, reevaluate=False):
    ollama = answers if isinstance(answers, ScriptedOllama) else ScriptedOllama(answers)
    with ollama.analyser() as analyser:
        return JobProcessor(store, analyser).process_batch(messages, reevaluate=reevaluate)


def applications(store):
    return [
        (application.company, application.job_title, application.status)
        for application in store.list_applications()
    ]


def rows(store, query):
    with store.connect() as connection:
        return [dict(row) for row in connection.execute(query).fetchall()]


def test_every_decision_is_stored_and_reported_in_its_group(store) -> None:
    messages = [
        message("m1", 1, "Your application to Data Analyst at Kestrel Analytics"),
        message("m2", 2, "Five articles on balcony gardening"),
        message("m3", 3, "We received your application"),
    ]
    results = run(
        store,
        {
            messages[0].subject: update("Kestrel Analytics", "Data Analyst", "applied"),
            messages[1].subject: other("not_application"),
            messages[2].subject: other("needs_review", ["company", "job_title"]),
        },
        *messages,
    )

    report = run_report(results, {item.source.message_id: item for item in messages})
    assert report["summary"] == {
        "status_update": ["m1"],
        "not_application": ["m2"],
        "review": ["m3"],
        "skipped": [],
        "failed": [],
        "deferred": [],
    }
    assert report["applications"] == {"created": 1, "advanced": 0, "reopened": 0, "unchanged": 0}
    assert applications(store) == [("Kestrel Analytics", "Data Analyst", APPLIED)]
    [item] = store.list_review_items()
    assert (item.message_id, item.cause, item.missing_fields) == (
        "m3",
        ReviewCause.ANALYSER,
        ["company", "job_title"],
    )
    decisions = rows(store, "SELECT message_id, decision FROM analyses ORDER BY id")
    assert [row["decision"] for row in decisions] == [
        "status_update",
        "not_application",
        "needs_review",
    ]


def test_known_ids_are_skipped_before_any_model_call(store) -> None:
    first = message("m1", 1, "Your application to Data Analyst at Kestrel Analytics")
    ollama = ScriptedOllama({first.subject: update("Kestrel Analytics", "Data Analyst", "applied")})
    run(store, ollama, first)

    results = run(store, ollama, first, first)

    assert len(ollama.calls) == 1
    assert [result.status for result in results] == [ProcessingStatus.SKIPPED] * 2
    assert len(rows(store, "SELECT * FROM links")) == 1
    report = run_report(results, {"m1": first}, skipped=["m0"], deferred=["m9"])
    assert report["summary"]["skipped"] == ["m0", "m1", "m1"]
    assert report["summary"]["deferred"] == ["m9"]


def test_updates_to_one_application_replay_in_received_order(store) -> None:
    applied = message("m1", 1, "Application sent to Kestrel Analytics Sdn. Bhd.: Data Analyst")
    interview = message("m3", 3, "Kestrel Analytics invites you to interview: Data Analyst")
    # Viewed arrives out of order, after the interview, and writes the company in capitals.
    viewed = message(
        "m2", 2, "Your Data Analyst application was viewed by KESTREL ANALYTICS SDN. BHD."
    )
    results = run(
        store,
        {
            applied.subject: update("Kestrel Analytics Sdn. Bhd.", "Data Analyst", "applied"),
            interview.subject: update("Kestrel Analytics", "Data Analyst", "interview"),
            viewed.subject: update("KESTREL ANALYTICS SDN. BHD.", "Data Analyst", "viewed"),
        },
        applied,
        interview,
        viewed,
    )

    assert [result.effect.value for result in results] == ["created", "advanced", "unchanged"]
    [application] = store.list_applications()
    assert application.company == "Kestrel Analytics Sdn. Bhd."
    assert application.status is INTERVIEW
    # 2026-05-03 02:00 UTC is 10:00 the same day in Malaysia.
    assert application.latest_update == date(2026, 5, 3)
    links = rows(store, "SELECT message_id, application_id FROM links ORDER BY message_id")
    assert [(row["message_id"], row["application_id"]) for row in links] == [
        ("m1", application.id),
        ("m2", application.id),
        ("m3", application.id),
    ]


def test_two_roles_at_one_company_are_two_applications(store) -> None:
    analyst = message("m1", 1, "Lumen Works: Backend Engineer received")
    designer = message("m2", 2, "Lumen Works: Product Designer received")
    run(
        store,
        {
            analyst.subject: update("Lumen Works", "Backend Engineer", "applied"),
            designer.subject: update("Lumen Works", "Product Designer", "applied"),
        },
        analyst,
        designer,
    )

    assert sorted(applications(store)) == [
        ("Lumen Works", "Backend Engineer", APPLIED),
        ("Lumen Works", "Product Designer", APPLIED),
    ]


def test_a_reapplication_after_a_rejection_reopens_the_application(store) -> None:
    subjects = {
        "m1": ("Lumen Works: Backend Engineer received", "applied"),
        "m2": ("Lumen Works: Backend Engineer declined", "rejected"),
        "m3": ("Lumen Works: Backend Engineer received again", "applied"),
    }
    messages = [
        message(mid, day, subject) for day, (mid, (subject, _)) in enumerate(subjects.items(), 1)
    ]
    results = run(
        store,
        {
            subject: update("Lumen Works", "Backend Engineer", status)
            for subject, status in subjects.values()
        },
        *messages,
    )

    assert [result.effect.value for result in results] == ["created", "advanced", "reopened"]
    assert applications(store) == [("Lumen Works", "Backend Engineer", APPLIED)]


def test_a_contradictory_outcome_waits_for_review_and_confirming_it_applies(store) -> None:
    offer = message("m1", 1, "Offer: Platform Engineer at Brightfield Systems")
    rejection = message("m2", 2, "Brightfield Systems: Platform Engineer update")
    results = run(
        store,
        {
            offer.subject: update("Brightfield Systems", "Platform Engineer", "offered"),
            rejection.subject: update("Brightfield Systems", "Platform Engineer", "rejected"),
        },
        offer,
        rejection,
    )

    assert [result.group for result in results] == ["status_update", "review"]
    [item] = store.list_review_items()
    assert item.cause is ReviewCause.CONTRADICTORY_OUTCOME
    assert item.suggested is not None and item.suggested.status is OFFERED
    assert item.extracted.status is REJECTED

    resolved = store.resolve_review(
        item.id,
        ResolutionAction.ATTACH,
        application_id=item.suggested.id,
        company=item.extracted.company,
        job_title=item.extracted.job_title,
        status=REJECTED,
    )

    assert resolved is not None and resolved.status is REJECTED
    assert store.list_review_items() == []
    assert rows(store, "SELECT COUNT(*) AS n FROM aliases") == [{"n": 0}]


def test_a_likely_match_waits_for_review_and_accepting_it_saves_an_alias(store) -> None:
    brand = message("m1", 1, "Orchid Harbour received your Product Designer application")
    group = message("m2", 2, "Orchid Harbour Group: interview for Product Designer")
    later = message("m3", 3, "Orchid Harbour Group: Product Designer outcome")
    ollama = ScriptedOllama(
        {
            brand.subject: update("Orchid Harbour", "Product Designer", "applied"),
            group.subject: update("Orchid Harbour Group", "Product Designer", "interview"),
            later.subject: update("Orchid Harbour Group", "Product Designer", "rejected"),
        }
    )
    run(store, ollama, brand, group)
    [item] = store.list_review_items()
    assert item.cause is ReviewCause.LIKELY_MATCH
    assert item.suggested is not None and item.suggested.company == "Orchid Harbour"

    store.resolve_review(
        item.id,
        ResolutionAction.ATTACH,
        application_id=item.suggested.id,
        company="Orchid Harbour Group",
        job_title="Product Designer",
        status=INTERVIEW,
    )
    [result] = run(store, ollama, later)

    assert result.group == "status_update"
    assert applications(store) == [("Orchid Harbour", "Product Designer", REJECTED)]
    assert rows(store, "SELECT company FROM aliases") == [{"company": "Orchid Harbour Group"}]


def test_the_guard_and_the_grounding_check_keep_the_original_output(store) -> None:
    guarded = message("m1", 1, "Thanks for applying: Data Analyst")
    ungrounded = message("m2", 2, "Thanks for applying: Backend Engineer")
    raw = json.loads(update("unknown", "Data Analyst", "applied"))
    invented = update("Invented Company", "Backend Engineer", "applied")
    run(
        store,
        {guarded.subject: json.dumps(raw), ungrounded.subject: invented},
        guarded,
        ungrounded,
    )

    analyses = rows(
        store,
        "SELECT message_id, decision, reason, missing_fields_json, company, job_title, status, "
        "missing_value_replaced, grounding_replaced, original_output FROM analyses ORDER BY id",
    )
    assert analyses[0] | {"original_output": None} == {
        "message_id": "m1",
        "decision": "needs_review",
        "reason": MISSING_VALUE_REASON,
        "missing_fields_json": '["company"]',
        "company": "unknown",
        "job_title": "Data Analyst",
        "status": "applied",
        "missing_value_replaced": 1,
        "grounding_replaced": 0,
        "original_output": None,
    }
    assert json.loads(analyses[0]["original_output"]) == raw
    assert analyses[1]["reason"] == GROUNDING_FAILURE_REASON
    assert analyses[1]["missing_fields_json"] == '["company"]'
    assert (analyses[1]["company"], analyses[1]["grounding_replaced"]) == ("Invented Company", 1)
    assert json.loads(analyses[1]["original_output"]) == json.loads(invented)
    assert store.list_applications() == []
    assert [item.extracted.job_title for item in store.list_review_items()] == [
        "Data Analyst",
        "Backend Engineer",
    ]


@pytest.mark.parametrize("answer", ["not json", other("status_update"), TIMEOUT])
def test_an_invalid_output_fails_and_the_third_failure_goes_to_review(store, answer) -> None:
    broken = message("m1", 1, "Lumen Works: Backend Engineer received")
    ollama = ScriptedOllama({broken.subject: answer})

    first = run(store, ollama, broken)
    second = run(store, ollama, broken)
    [third] = run(store, ollama, broken)

    assert [result.status for result in first + second] == [ProcessingStatus.FAILED] * 2
    assert (third.status, third.group) == (ProcessingStatus.PROCESSED, "review")
    [item] = store.list_review_items()
    assert (item.reason, item.missing_fields) == (REPEATED_FAILURE_REASON, ["message_content"])
    assert store.list_applications() == []


def test_an_overlong_message_goes_to_review(store) -> None:
    long = message("m1", 1, "A very long message")
    [result] = run(store, {long.subject: OVERFLOW}, long)

    assert result.group == "review"
    [item] = store.list_review_items()
    assert (item.reason, item.missing_fields) == (TOO_LONG_REASON, ["message_content"])


def test_the_model_reason_is_sanitized(store) -> None:
    rejected = message("m1", 1, "Lumen Works: Backend Engineer declined")
    reason = "Lumen Works declined Backend Engineer, see https://lumen.example.invalid/x"
    run(
        store,
        {rejected.subject: update("Lumen Works", "Backend Engineer", "rejected", reason)},
        rejected,
    )

    assert rows(store, "SELECT reason FROM analyses") == [
        {"reason": "[redacted] declined [redacted], see [redacted]"}
    ]


def _review_item(store, subject_answers):
    messages = [message(f"m{day}", day, subject) for day, subject in enumerate(subject_answers, 1)]
    run(store, dict(zip((item.subject for item in messages), subject_answers.values())), *messages)
    return store.list_review_items()


def test_review_correction_matches_creates_or_attaches_without_changing_the_analysis(store) -> None:
    [item] = _review_item(
        store,
        {
            "Kestrel Analytics: Data Analyst received": update(
                "Kestrel Analytics", "Data Analyst", "applied"
            ),
            "Thanks for applying to Kestrel": other("needs_review", ["job_title"]),
        },
    )

    resolved = store.resolve_review(
        item.id,
        ResolutionAction.MATCH,
        company="KESTREL ANALYTICS",
        job_title="Data Analyst",
        status=VIEWED,
    )

    assert resolved is not None and resolved.status is VIEWED
    assert applications(store) == [("Kestrel Analytics", "Data Analyst", VIEWED)]
    assert rows(store, "SELECT action, application_id FROM resolutions") == [
        {"action": "match", "application_id": resolved.id}
    ]
    assert rows(store, "SELECT confirmed FROM links WHERE message_id = 'm2'") == [{"confirmed": 1}]
    assert rows(store, "SELECT decision, active FROM analyses WHERE message_id = 'm2'") == [
        {"decision": "needs_review", "active": 1}
    ]
    with pytest.raises(ReviewItemResolvedError):
        store.resolve_review(item.id, ResolutionAction.NOT_APPLICATION)


def test_refused_resolutions_save_nothing(store) -> None:
    items = _review_item(
        store,
        {
            "Orchid Harbour: Product Designer received": update(
                "Orchid Harbour", "Product Designer", "applied"
            ),
            "Lumen Works: Backend Engineer received": update(
                "Lumen Works", "Backend Engineer", "applied"
            ),
            "Thanks for applying": other("needs_review", ["company", "job_title"]),
        },
    )
    [item] = items
    orchid, lumen = sorted(store.list_applications(), key=lambda application: application.id)

    with pytest.raises(LikelyMatchError) as likely:
        store.resolve_review(
            item.id,
            ResolutionAction.MATCH,
            company="Orchid Harbour Group",
            job_title="Product Designer",
            status=INTERVIEW,
        )
    assert likely.value.application.id == orchid.id
    with pytest.raises(JobReviewError, match="already application"):
        store.resolve_review(
            item.id,
            ResolutionAction.CREATE,
            company="Lumen Works Sdn Bhd",
            job_title="Backend Engineer",
            status=INTERVIEW,
        )
    with pytest.raises(JobReviewError, match="belong to application"):
        store.resolve_review(
            item.id,
            ResolutionAction.ATTACH,
            application_id=orchid.id,
            company="Lumen Works",
            job_title="Backend Engineer",
            status=INTERVIEW,
        )
    with pytest.raises(JobReviewError, match="letter or digit|normalized key"):
        store.resolve_review(
            item.id, ResolutionAction.CREATE, company="---", job_title="Designer", status=APPLIED
        )

    assert rows(store, "SELECT COUNT(*) AS n FROM resolutions") == [{"n": 0}]
    assert len(store.list_review_items()) == 1
    assert [application.status for application in (orchid, lumen)] == [APPLIED, APPLIED]

    store.resolve_review(
        item.id,
        ResolutionAction.CREATE,
        company="Orchid Harbour Group",
        job_title="Product Designer",
        status=INTERVIEW,
    )
    assert len(store.list_applications()) == 3


def test_not_an_application_changes_no_application(store) -> None:
    [item] = _review_item(store, {"An unclear email": other("needs_review", ["status"])})

    assert store.resolve_review(item.id, ResolutionAction.NOT_APPLICATION) is None
    assert store.list_review_items() == []
    assert store.list_applications() == []
    assert rows(store, "SELECT COUNT(*) AS n FROM links") == [{"n": 0}]


def test_reevaluation_replaces_the_link_and_removes_an_emptied_application(store) -> None:
    first = message("m1", 1, "Lumen Works: Backend Engineer received")
    ollama = ScriptedOllama({first.subject: update("Lumen Works", "Backend Engineer", "applied")})
    run(store, ollama, first)

    ollama.answers[first.subject] = other("needs_review", ["status"])
    [result] = run(store, ollama, first, reevaluate=True)

    assert result.group == "review"
    assert store.list_applications() == []
    assert rows(store, "SELECT COUNT(*) AS n FROM links") == [{"n": 0}]
    assert rows(store, "SELECT revision, active FROM analyses ORDER BY id") == [
        {"revision": 1, "active": 0},
        {"revision": 2, "active": 1},
    ]

    ollama.answers[first.subject] = other("not_application")
    run(store, ollama, first, reevaluate=True)
    assert store.list_review_items() == []


def test_the_job_database_shares_no_table_with_the_expense_database(tmp_path, store) -> None:
    expense = Store(tmp_path / "kakeibo.sqlite3", tmp_path / "ledger.csv")

    def tables(path):
        with sqlite3.connect(path) as connection:
            return {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }

    job_tables = tables(store.database_path)
    expense_tables = tables(expense.database_path)
    assert {"applications", "aliases", "links", "review_items"} <= job_tables
    assert "transactions" not in job_tables
    assert {"applications", "links", "review_items"}.isdisjoint(expense_tables)


@pytest.mark.parametrize(
    ("sender", "subject", "listed"),
    [
        ("LinkedIn <jobs-noreply@linkedin.com>", "Example hiring: Analyst", True),
        ("Jobstreet <noreply@e.jobstreet.com>", "Jobs for you", True),
        ("Example <no-reply@apply.workable.com>", "Thank you", True),
        ("Example <careers@example.invalid>", "Your Application was received", True),
        ("Example <careers@example.invalid>", "Interview reminder", True),
        ("LinkedIn <jobalerts-noreply@linkedin.com>", "Analyst at Example", False),
        ("Jobstreet <noreply@email.jobstreet.com>", "Career advice", False),
        ("Shop <hello@shop.example.invalid>", "Your order has shipped", False),
    ],
)
def test_the_skim_list_takes_job_related_senders_and_subjects(sender, subject, listed) -> None:
    assert looks_job_related(message("m1", 1, subject, sender=sender)) is listed


def test_the_run_result_lists_job_related_not_application_emails(store) -> None:
    alert = message("m1", 1, "Your application status digest", sender="Board <a@board.invalid>")
    other_mail = message("m2", 2, "Weekly reading list")
    results = run(
        store,
        {alert.subject: other("not_application"), other_mail.subject: other("not_application")},
        alert,
        other_mail,
    )

    report = run_report(results, {"m1": alert, "m2": other_mail})
    assert report["skim"] == [
        {"message_id": "m1", "sender": alert.sender, "subject": alert.subject}
    ]
