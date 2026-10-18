"""Matching and status changes (contract §6 and §7) as plain functions over a tracker store.

The functions read and write only through `TrackerStore`, so the evaluator's in-memory store
and the later SQLite store share them unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from .models import JobAnalysisOutput, JobDecision, JobStatus
from .names import company_key, contains_words, job_title_key

KeyPair = tuple[str, str]

_RANK = {
    JobStatus.APPLIED: 1,
    JobStatus.VIEWED: 2,
    JobStatus.INTERVIEW: 3,
    JobStatus.OFFERED: 4,
    JobStatus.REJECTED: 4,
}
_OUTCOMES = {JobStatus.OFFERED, JobStatus.REJECTED}


class LinkEffect(str, Enum):
    CREATED = "created"
    ADVANCED = "advanced"
    REOPENED = "reopened"
    UNCHANGED = "unchanged"


class ReviewCause(str, Enum):
    ANALYSER = "analyser"
    LIKELY_MATCH = "likely_match"
    CONTRADICTORY_OUTCOME = "contradictory_outcome"


@dataclass(frozen=True)
class StatusUpdate:
    """One message's status for an application, as linked or about to be linked."""

    message_id: str
    received_at: datetime
    status: JobStatus
    confirmed: bool = False


@dataclass(frozen=True)
class Application:
    id: int
    company: str
    job_title: str
    pair: KeyPair


@dataclass(frozen=True)
class Link:
    application_id: int
    update: StatusUpdate
    effect: LinkEffect


@dataclass(frozen=True)
class ReviewItem:
    message_id: str
    cause: ReviewCause
    suggested_application_id: int | None = None


@dataclass(frozen=True)
class Outcome:
    """What one decision did: a link to an application, a review item, or neither."""

    link: Link | None = None
    review: ReviewItem | None = None


class TrackerStore(Protocol):
    def applications(self) -> Sequence[Application]: ...

    def aliases(self, application_id: int) -> Sequence[KeyPair]: ...

    def links(self, application_id: int) -> Sequence[Link]: ...

    def status(self, application_id: int) -> JobStatus | None: ...

    def create_application(self, company: str, job_title: str, pair: KeyPair) -> Application: ...

    def add_link(self, link: Link, status: JobStatus) -> None:
        """Store a link and the application's recomputed status."""
        ...

    def add_review_item(self, item: ReviewItem) -> None: ...


def key_pair(company: str, job_title: str) -> KeyPair:
    return company_key(company), job_title_key(job_title)


def is_contradictory(current: JobStatus | None, update: JobStatus) -> bool:
    """Whether an update is `offered` against `rejected`, or `rejected` against `offered`."""
    return current is not None and {current, update} == _OUTCOMES


def next_status(current: JobStatus | None, update: StatusUpdate) -> JobStatus:
    if current is None:
        return update.status
    if current is JobStatus.REJECTED and update.status is JobStatus.APPLIED:
        return JobStatus.APPLIED
    if is_contradictory(current, update.status):
        return update.status if update.confirmed else current
    if _RANK[update.status] > _RANK[current]:
        return update.status
    return current


def replay_status(updates: Iterable[StatusUpdate]) -> JobStatus | None:
    """Replay status updates in received order, then by message ID, from no status."""
    status = None
    for update in sorted(updates, key=lambda item: (item.received_at, item.message_id)):
        status = next_status(status, update)
    return status


def link_effect(before: JobStatus | None, after: JobStatus) -> LinkEffect:
    if before is None:
        return LinkEffect.CREATED
    if before is after:
        return LinkEffect.UNCHANGED
    if before is JobStatus.REJECTED and after is JobStatus.APPLIED:
        return LinkEffect.REOPENED
    return LinkEffect.ADVANCED


def _pairs(store: TrackerStore, application: Application) -> list[KeyPair]:
    return [application.pair, *store.aliases(application.id)]


def _words_within(left: str, right: str) -> bool:
    return contains_words(left, right) or contains_words(right, left)


def _is_likely(pair: KeyPair, other: KeyPair) -> bool:
    (company, title), (other_company, other_title) = pair, other
    return (company == other_company and _words_within(title, other_title)) or (
        title == other_title and _words_within(company, other_company)
    )


def find_exact(store: TrackerStore, pair: KeyPair) -> Application | None:
    return next(
        (application for application in store.applications() if pair in _pairs(store, application)),
        None,
    )


def find_likely(store: TrackerStore, pair: KeyPair) -> Application | None:
    """Suggest the likely match whose latest linked message is newest, ties to the lower ID.

    Every application has a link: it is created with its first one.
    """
    candidates = [
        application
        for application in store.applications()
        if any(_is_likely(pair, other) for other in _pairs(store, application))
    ]
    return max(
        candidates,
        key=lambda application: (
            max(link.update.received_at for link in store.links(application.id)),
            -application.id,
        ),
        default=None,
    )


def link_update(store: TrackerStore, application: Application, update: StatusUpdate) -> Link:
    """Link an update to an application and recompute its status from every link."""
    links = store.links(application.id)
    before = replay_status(link.update for link in links)
    after = replay_status([*(link.update for link in links), update])
    assert after is not None
    link = Link(application.id, update, link_effect(before, after))
    store.add_link(link, after)
    return link


def _review(
    store: TrackerStore,
    message_id: str,
    cause: ReviewCause,
    suggested: Application | None = None,
) -> Outcome:
    review = ReviewItem(message_id, cause, suggested.id if suggested else None)
    store.add_review_item(review)
    return Outcome(review=review)


def apply_status_update(
    store: TrackerStore, company: str, job_title: str, update: StatusUpdate
) -> Outcome:
    """Match one status update and link it, create an application, or send it to review."""
    pair = key_pair(company, job_title)
    application = find_exact(store, pair)
    if application is not None:
        if not update.confirmed and is_contradictory(store.status(application.id), update.status):
            return _review(store, update.message_id, ReviewCause.CONTRADICTORY_OUTCOME, application)
        return Outcome(link=link_update(store, application, update))
    likely = find_likely(store, pair)
    if likely is not None:
        return _review(store, update.message_id, ReviewCause.LIKELY_MATCH, likely)
    created = store.create_application(company, job_title, pair)
    return Outcome(link=link_update(store, created, update))


def apply_decision(
    store: TrackerStore, message_id: str, received_at: datetime, output: JobAnalysisOutput
) -> Outcome:
    """Persist one validated, grounded decision without resolving any review item."""
    if output.decision is JobDecision.NOT_APPLICATION:
        return Outcome()
    if output.decision is JobDecision.NEEDS_REVIEW or output.application is None:
        return _review(store, message_id, ReviewCause.ANALYSER)
    application = output.application
    return apply_status_update(
        store,
        application.company,
        application.job_title,
        StatusUpdate(message_id, received_at, application.status),
    )


@dataclass
class InMemoryStore:
    """An empty tracker store that lives only for one evaluation replay."""

    _applications: list[Application] = field(default_factory=list)
    _aliases: dict[int, list[KeyPair]] = field(default_factory=dict)
    _links: dict[int, list[Link]] = field(default_factory=dict)
    _statuses: dict[int, JobStatus] = field(default_factory=dict)
    review_items: list[ReviewItem] = field(default_factory=list)

    def applications(self) -> Sequence[Application]:
        return list(self._applications)

    def aliases(self, application_id: int) -> Sequence[KeyPair]:
        return list(self._aliases.get(application_id, []))

    def links(self, application_id: int) -> Sequence[Link]:
        return list(self._links.get(application_id, []))

    def status(self, application_id: int) -> JobStatus | None:
        return self._statuses.get(application_id)

    def _owner(self, pair: KeyPair) -> int | None:
        return next(
            (
                application.id
                for application in self._applications
                if pair == application.pair or pair in self._aliases.get(application.id, [])
            ),
            None,
        )

    def create_application(self, company: str, job_title: str, pair: KeyPair) -> Application:
        if self._owner(pair) is not None:
            raise ValueError("key pair already belongs to an application")
        application = Application(len(self._applications) + 1, company, job_title, pair)
        self._applications.append(application)
        return application

    def add_alias(self, application_id: int, company: str, job_title: str) -> None:
        pair = key_pair(company, job_title)
        if self._owner(pair) is not None:
            raise ValueError("key pair already belongs to an application")
        self._aliases.setdefault(application_id, []).append(pair)

    def add_link(self, link: Link, status: JobStatus) -> None:
        self._links.setdefault(link.application_id, []).append(link)
        self._statuses[link.application_id] = status

    def add_review_item(self, item: ReviewItem) -> None:
        self.review_items.append(item)
