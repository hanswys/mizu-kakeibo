"""Company and job title keys, and the text folding the grounding check uses (contract §5)."""

from __future__ import annotations

import re
import unicodedata

_MALAYSIAN_FORMS = ("sdn bhd", "sdn berhad", "sendirian berhad", "bhd", "berhad")
_OTHER_FORMS = (
    "pte ltd",
    "pte limited",
    "private limited",
    "pvt ltd",
    "co ltd",
    "ltd",
    "limited",
    "inc",
    "incorporated",
    "llc",
    "plc",
    "corp",
    "corporation",
    "gmbh",
)
# Longest first, so `m sdn bhd` is removed whole rather than leaving `m` behind `sdn bhd`.
LEGAL_FORMS = tuple(
    sorted(
        {*_MALAYSIAN_FORMS, *(f"m {form}" for form in _MALAYSIAN_FORMS), *_OTHER_FORMS},
        key=lambda form: (-len(form), form),
    )
)

_APOSTROPHES = {"'", "’"}


def fold_text(value: str) -> str:
    """Apply steps 1 to 4: NFKC and case folding, then reduce the text to single-spaced words."""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(
        character
        for character in value
        if character not in _APOSTROPHES and unicodedata.category(character) != "Cf"
    )
    value = value.replace("&", " and ")
    value = "".join(character if character.isalnum() else " " for character in value)
    return re.sub(r"\s+", " ", value).strip()


def job_title_key(value: str) -> str:
    return fold_text(value)


def company_key(value: str) -> str:
    """Fold a company name and remove one trailing legal form when a word remains before it."""
    key = fold_text(value)
    for form in LEGAL_FORMS:
        if key.endswith(f" {form}"):
            return key[: -len(form) - 1]
    return key


def contains_words(text: str, words: str) -> bool:
    """Whether the folded `words` occur as a whole-word sequence in the folded `text`."""
    return bool(words) and f" {words} " in f" {text} "
