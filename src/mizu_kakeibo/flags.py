from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Protocol

from .storage import FlagCandidate, LedgerEntry

DUPLICATE = "duplicate"
UNUSUAL = "unusual"
UNUSUAL_EARLIER_CHARGES = 3  # a merchant needs this many earlier charges before one is unusual
UNUSUAL_FACTOR = 2  # and the amount must be more than this many times their median


class Charge(Protocol):
    @property
    def merchant(self) -> str: ...

    @property
    def amount_minor(self) -> int: ...

    @property
    def currency(self) -> str: ...

    @property
    def transaction_date(self) -> date: ...


@dataclass(frozen=True)
class Flag:
    """A logged expense to check, with why: an earlier possible duplicate, an unusual amount."""

    candidate: FlagCandidate
    duplicate_of: LedgerEntry | None
    usual_minor: int | None
    earlier_charges: int

    @property
    def entry(self) -> LedgerEntry:
        return self.candidate.entry

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(
            kind
            for kind, present in (
                (DUPLICATE, self.duplicate_of is not None),
                (UNUSUAL, self.usual_minor is not None),
            )
            if present
        )


def fold_merchant(merchant: str) -> str:
    """Match merchant names exactly, ignoring case, width, and repeated spaces."""
    return " ".join(unicodedata.normalize("NFKC", merchant).casefold().split())


def is_possible_duplicate(a: Charge, b: Charge) -> bool:
    """Same merchant, amount, currency, and day: the ledger has no time to tell them apart."""
    return (
        fold_merchant(a.merchant) == fold_merchant(b.merchant)
        and a.amount_minor == b.amount_minor
        and a.currency == b.currency
        and a.transaction_date == b.transaction_date
    )


def usual_amount(amount_minor: int, earlier: Sequence[int]) -> int | None:
    """The merchant's usual amount, the median of its earlier charges, when this one is unusual."""
    if len(earlier) < UNUSUAL_EARLIER_CHARGES:
        return None
    usual = median(earlier)
    return round(usual) if amount_minor > UNUSUAL_FACTOR * usual else None


def find_flags(candidates: Sequence[FlagCandidate]) -> list[Flag]:
    """Work out the flags waiting on the logged expenses, in the order they are given.

    Of a possible duplicate pair, only the expense logged later is flagged. Its unusual
    amount is measured against the same merchant's charges in the same currency dated
    before it. A kind the expense was not checked for, or the user kept, is never flagged.
    """
    by_merchant: dict[tuple[str, str], list[FlagCandidate]] = {}
    for candidate in candidates:
        key = (fold_merchant(candidate.entry.merchant), candidate.entry.currency)
        by_merchant.setdefault(key, []).append(candidate)
    flags = []
    for candidate in candidates:
        open_kinds = candidate.checks - candidate.kept
        if not open_kinds:
            continue
        entry = candidate.entry
        others = [
            other
            for other in by_merchant[(fold_merchant(entry.merchant), entry.currency)]
            if other is not candidate
        ]
        duplicate_of = None
        if DUPLICATE in open_kinds:
            earlier = [
                other
                for other in others
                if other.logged < candidate.logged and is_possible_duplicate(other.entry, entry)
            ]
            if earlier:
                duplicate_of = min(earlier, key=lambda other: other.logged).entry
        amounts = [
            other.entry.amount_minor
            for other in others
            if other.entry.transaction_date < entry.transaction_date
        ]
        usual = usual_amount(entry.amount_minor, amounts) if UNUSUAL in open_kinds else None
        if duplicate_of is not None or usual is not None:
            flags.append(Flag(candidate, duplicate_of, usual, len(amounts)))
    return flags
