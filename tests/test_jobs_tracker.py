from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from mizu_kakeibo.jobs.models import JobAnalysisOutput, JobStatus
from mizu_kakeibo.jobs.tracker import (
    InMemoryStore,
    LinkEffect,
    ReviewCause,
    StatusUpdate,
    apply_decision,
    apply_status_update,
    key_pair,
    next_status,
    replay_status,
)

APPLIED, VIEWED, INTERVIEW, REJECTED, OFFERED = (
    JobStatus.APPLIED,
    JobStatus.VIEWED,
    JobStatus.INTERVIEW,
    JobStatus.REJECTED,
    JobStatus.OFFERED,
)
START = datetime(2026, 5, 1, tzinfo=timezone.utc)


def at(day: int) -> datetime:
    return START + timedelta(days=day)


def su(message_id, day, status, confirmed=False):
    return StatusUpdate(message_id, at(day), status, confirmed)


def statuses(*steps):
    return replay_status(su(f"m{index}", index, status) for index, status in enumerate(steps))


@pytest.mark.parametrize(
    ("steps", "final"),
    [
        ((REJECTED,), REJECTED),
        ((APPLIED, VIEWED, INTERVIEW), INTERVIEW),
        ((APPLIED, INTERVIEW, VIEWED), INTERVIEW),
        ((APPLIED, APPLIED), APPLIED),
        ((APPLIED, REJECTED), REJECTED),
        ((VIEWED, OFFERED), OFFERED),
        ((APPLIED, REJECTED, APPLIED), APPLIED),
        ((APPLIED, REJECTED, VIEWED), REJECTED),
        ((APPLIED, REJECTED, APPLIED, REJECTED), REJECTED),
        ((OFFERED, APPLIED), OFFERED),
        ((INTERVIEW, REJECTED), REJECTED),
        ((OFFERED, REJECTED), OFFERED),
        ((REJECTED, OFFERED), REJECTED),
        ((OFFERED, OFFERED), OFFERED),
    ],
)
def test_status_replay_rules(steps, final) -> None:
    assert statuses(*steps) is final


def test_confirmed_contradictory_step_replaces_the_outcome() -> None:
    assert next_status(OFFERED, su("m", 1, REJECTED, confirmed=True)) is REJECTED
    assert next_status(REJECTED, su("m", 1, OFFERED, confirmed=True)) is OFFERED
    assert next_status(OFFERED, su("m", 1, REJECTED)) is OFFERED


def test_replay_orders_by_received_time_then_message_id() -> None:
    assert replay_status([su("m2", 2, REJECTED), su("m1", 1, APPLIED)]) is REJECTED
    same_time = [su("b", 1, APPLIED), su("a", 1, REJECTED)]
    assert replay_status(same_time) is APPLIED
    assert replay_status(reversed(same_time)) is APPLIED
    assert replay_status([]) is None


def link(store, company, job_title, message_id, day, status, confirmed=False):
    return apply_status_update(store, company, job_title, su(message_id, day, status, confirmed))


def test_creation_links_the_first_update_whatever_its_status() -> None:
    store = InMemoryStore()

    outcome = link(store, "Example Company Sdn. Bhd.", "Software Engineer", "m1", 1, REJECTED)

    assert outcome.link is not None
    assert outcome.link.effect is LinkEffect.CREATED
    (application,) = store.applications()
    assert (application.company, application.job_title) == (
        "Example Company Sdn. Bhd.",
        "Software Engineer",
    )
    assert application.pair == ("example company", "software engineer")
    assert store.status(application.id) is REJECTED


def test_exact_match_ignores_legal_form_case_and_spacing() -> None:
    store = InMemoryStore()
    link(store, "Example Company Sdn. Bhd.", "Software Engineer", "m1", 1, APPLIED)

    viewed = link(store, "EXAMPLE COMPANY SDN. BHD.", "Software  Engineer", "m2", 2, VIEWED)
    late = link(store, "example company", "software engineer", "m0", 0, APPLIED)

    assert len(store.applications()) == 1
    assert viewed.link and viewed.link.effect is LinkEffect.ADVANCED
    assert late.link and late.link.effect is LinkEffect.UNCHANGED
    assert store.status(1) is VIEWED
    assert store.applications()[0].company == "Example Company Sdn. Bhd."


def test_reapplication_after_rejection_reopens() -> None:
    store = InMemoryStore()
    link(store, "Example", "Engineer", "m1", 1, APPLIED)
    link(store, "Example", "Engineer", "m2", 2, REJECTED)

    outcome = link(store, "Example", "Engineer", "m3", 3, APPLIED)

    assert outcome.link and outcome.link.effect is LinkEffect.REOPENED
    assert store.status(1) is APPLIED
    assert [item.update.message_id for item in store.links(1)] == ["m1", "m2", "m3"]


def test_out_of_order_update_lands_in_its_received_position() -> None:
    store = InMemoryStore()
    link(store, "Example", "Engineer", "m1", 1, APPLIED)
    link(store, "Example", "Engineer", "m3", 3, APPLIED)

    outcome = link(store, "Example", "Engineer", "m2", 2, REJECTED)

    # Replayed in order, the re-application on day 3 still reopens the application.
    assert outcome.link and outcome.link.effect is LinkEffect.UNCHANGED
    assert store.status(1) is APPLIED


@pytest.mark.parametrize(("current", "update"), [(OFFERED, REJECTED), (REJECTED, OFFERED)])
def test_contradictory_outcome_goes_to_review_and_keeps_the_status(current, update) -> None:
    store = InMemoryStore()
    link(store, "Example", "Engineer", "m1", 1, current)

    outcome = link(store, "Example", "Engineer", "m2", 2, update)

    assert outcome.link is None
    assert outcome.review is not None
    assert outcome.review.cause is ReviewCause.CONTRADICTORY_OUTCOME
    assert outcome.review.suggested_application_id == 1
    assert store.status(1) is current
    assert len(store.links(1)) == 1
    assert store.review_items == [outcome.review]


def test_confirmed_contradictory_update_is_linked_and_replaces_the_status() -> None:
    store = InMemoryStore()
    link(store, "Example", "Engineer", "m1", 1, OFFERED)

    outcome = link(store, "Example", "Engineer", "m2", 2, REJECTED, confirmed=True)

    assert outcome.link and outcome.link.effect is LinkEffect.ADVANCED
    assert store.status(1) is REJECTED


def test_alias_is_an_exact_match() -> None:
    store = InMemoryStore()
    link(store, "Example", "Engineer", "m1", 1, APPLIED)
    store.add_alias(1, "Example Holdings Group", "Engineer - Malaysia")

    outcome = link(store, "Example Holdings Group Sdn Bhd", "Engineer Malaysia", "m2", 2, INTERVIEW)

    assert outcome.link and outcome.link.application_id == 1
    assert store.status(1) is INTERVIEW


def test_a_key_pair_belongs_to_one_application() -> None:
    store = InMemoryStore()
    link(store, "Example", "Engineer", "m1", 1, APPLIED)
    link(store, "Other", "Engineer", "m2", 2, APPLIED)
    with pytest.raises(ValueError, match="already belongs"):
        store.add_alias(2, "Example Ltd", "Engineer")
    with pytest.raises(ValueError, match="already belongs"):
        store.create_application("Example", "Engineer", key_pair("Example", "Engineer"))


@pytest.mark.parametrize(
    ("company", "job_title"),
    [
        ("Example", "Senior Software Engineer"),
        ("Example", "Engineer"),
        ("Example Group", "Software Engineer"),
    ],
)
def test_likely_match_goes_to_review_with_the_suggestion(company, job_title) -> None:
    store = InMemoryStore()
    link(store, "Example Sdn Bhd", "Software Engineer", "m1", 1, APPLIED)

    outcome = link(store, company, job_title, "m2", 2, INTERVIEW)

    assert outcome.link is None
    assert outcome.review is not None
    assert outcome.review.cause is ReviewCause.LIKELY_MATCH
    assert outcome.review.suggested_application_id == 1
    assert len(store.applications()) == 1
    assert store.status(1) is APPLIED


def test_likely_match_works_both_ways() -> None:
    store = InMemoryStore()
    link(store, "Example Group", "Senior Software Engineer", "m1", 1, APPLIED)

    by_title = link(store, "Example Group", "Software Engineer", "m2", 2, VIEWED)
    by_company = link(store, "Example", "Senior Software Engineer", "m3", 3, VIEWED)

    assert by_title.review and by_title.review.cause is ReviewCause.LIKELY_MATCH
    assert by_company.review and by_company.review.cause is ReviewCause.LIKELY_MATCH


def test_likely_match_compares_aliases_too() -> None:
    store = InMemoryStore()
    link(store, "Example", "Engineer", "m1", 1, APPLIED)
    store.add_alias(1, "Example Group", "Platform Engineer")

    outcome = link(store, "Example Group", "Senior Platform Engineer", "m2", 2, VIEWED)

    assert outcome.review and outcome.review.suggested_application_id == 1


@pytest.mark.parametrize(
    ("company", "job_title"),
    [
        ("Example", "Software Developer"),
        ("Example", "Engineering Manager"),
        ("Examples", "Software Engineer"),
        ("Example Group", "Senior Software Engineer"),
        ("Another", "Software Engineer"),
    ],
)
def test_other_pairs_create_an_application(company, job_title) -> None:
    store = InMemoryStore()
    link(store, "Example", "Software Engineer", "m1", 1, APPLIED)

    outcome = link(store, company, job_title, "m2", 2, APPLIED)

    assert outcome.link and outcome.link.effect is LinkEffect.CREATED
    assert len(store.applications()) == 2


def test_two_roles_at_one_company_are_two_applications() -> None:
    store = InMemoryStore()
    link(store, "Example", "Data Analyst", "m1", 1, APPLIED)
    link(store, "Example", "Data Engineer", "m2", 1, APPLIED)
    link(store, "Example", "Data Engineer", "m3", 2, REJECTED)

    assert [store.status(application.id) for application in store.applications()] == [
        APPLIED,
        REJECTED,
    ]


def test_suggestion_prefers_the_newest_latest_link_then_the_lower_id() -> None:
    store = InMemoryStore()
    link(store, "Example", "Platform Engineer", "m1", 1, APPLIED)
    link(store, "Example", "Data Engineer", "m2", 2, APPLIED)
    assert len(store.applications()) == 2

    newest = link(store, "Example", "Engineer", "m3", 4, INTERVIEW)
    assert newest.review and newest.review.suggested_application_id == 2

    link(store, "Example", "Platform Engineer", "m4", 2, VIEWED)
    tied = link(store, "Example", "Engineer", "m5", 5, INTERVIEW)
    assert tied.review and tied.review.suggested_application_id == 1


def decision(value, application=None, missing_fields=()):
    return JobAnalysisOutput.model_validate_json(
        json.dumps(
            {
                "contract_version": "job-analysis-output/v0",
                "decision": value,
                "reason": "Fictional",
                "application": application,
                "missing_fields": list(missing_fields),
            }
        )
    )


def test_only_status_updates_match_or_create_applications() -> None:
    store = InMemoryStore()

    nothing = apply_decision(store, "m1", at(1), decision("not_application"))
    review = apply_decision(
        store, "m2", at(2), decision("needs_review", missing_fields=["company"])
    )
    created = apply_decision(
        store,
        "m3",
        at(3),
        decision(
            "status_update", {"company": "Example", "job_title": "Engineer", "status": "applied"}
        ),
    )

    assert nothing.link is None and nothing.review is None
    assert review.review and review.review.cause is ReviewCause.ANALYSER
    assert review.review.suggested_application_id is None
    assert created.link and created.link.update == su("m3", 3, APPLIED)
    assert len(store.applications()) == 1
