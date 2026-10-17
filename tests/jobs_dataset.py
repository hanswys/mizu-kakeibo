"""A small synthetic job-tracker evaluation set in the contract §11 format.

Every name, address, and message is made up. Examples marked `synthetic: false` stand in for real
inbox emails, so they are stored as connector `RAW` responses; the `synthetic: true` group is
stored as envelopes. `python tests/jobs_dataset.py <path>` writes the set for a smoke run.
"""

from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any

START = datetime(2026, 5, 1, 2, 0, tzinfo=timezone.utc)
BOARD = "Job Board <jobs-noreply@board.example.invalid>"

# Long enough that the plain-text stub rule keeps the stub when HTML is not preferred.
BOARD_FOOTER = (
    "This email was intended for Alex Example. You are receiving notification emails about "
    "your job applications. Unsubscribe: https://board.example.invalid/unsubscribe?trk=1 "
    "Help: https://board.example.invalid/help?trk=1 Example Board Corporation, "
    "1 Placeholder Street, Example City."
)


def received(day: int) -> datetime:
    return START + timedelta(days=day - 1)


def raw_message(
    message_id: str, day: int, sender: str, subject: str, plain: str, html: str | None = None
) -> dict[str, Any]:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "Alex Example <alex@example.invalid>"
    message["Subject"] = subject
    message["Date"] = format_datetime(received(day))
    message.set_content(plain)
    if html is not None:
        message.add_alternative(html, subtype="html")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": str(int(received(day).timestamp() * 1000)),
        "labelIds": ["INBOX"],
        "raw": raw,
    }


def envelope(message_id: str, day: int, sender: str, subject: str, text: str) -> dict[str, Any]:
    return {
        "contract_version": "inbox-message/v0",
        "source": {"provider": "gmail", "message_id": message_id, "thread_id": None},
        "received_at": received(day).isoformat(),
        "sender": sender,
        "subject": subject,
        "content": {"text": text, "source_type": "text_plain", "truncated": False},
    }


def update(key: str, company: str, job_title: str, status: str) -> dict[str, Any]:
    return {
        "decision": "status_update",
        "application": {"company": company, "job_title": job_title, "status": status},
        "application_key": key,
        "missing_fields": [],
    }


def other(decision: str, missing_fields: list[str] | None = None) -> dict[str, Any]:
    return {
        "decision": decision,
        "application": None,
        "application_key": None,
        "missing_fields": missing_fields or [],
    }


def example(message: dict[str, Any], expected: dict[str, Any], synthetic: bool = False):
    return {"message": message, "synthetic": synthetic, "expected": expected}


def dataset() -> dict[str, Any]:
    examples = [
        example(
            raw_message(
                "job-01",
                1,
                BOARD,
                "Alex, your application was sent to Kestrel Analytics Sdn. Bhd.",
                "Your application was sent to Kestrel Analytics Sdn. Bhd.\n\nData Analyst\n"
                f"Kestrel Analytics Sdn. Bhd. - Kuala Lumpur\n\n{BOARD_FOOTER}",
                "<p>Your application was sent to Kestrel Analytics Sdn. Bhd.</p>"
                "<table><tr><td>Data Analyst</td><td>Kestrel Analytics Sdn. Bhd.</td>"
                f"<td>Kuala Lumpur</td></tr></table><p>{BOARD_FOOTER}</p>",
            ),
            update("A01", "Kestrel Analytics Sdn. Bhd.", "Data Analyst", "applied"),
        ),
        example(
            raw_message(
                "job-02",
                2,
                "Orchid Harbour Careers <careers@orchid.example.invalid>",
                "Thank you for applying to Product Designer",
                "Orchid Harbour has received your application for Product Designer.",
            ),
            update("A03", "Orchid Harbour", "Product Designer", "applied"),
        ),
        example(
            raw_message(
                "job-03",
                3,
                "Kestrel Analytics Recruiting <talent@kestrel.example.invalid>",
                "Interview invitation: Data Analyst",
                "Kestrel Analytics would like to invite you to an interview for the "
                "Data Analyst role. Please pick a time that suits you.",
            ),
            update("A01", "Kestrel Analytics", "Data Analyst", "interview"),
        ),
        # The job title is only in the HTML alternative, and the update arrives after interview.
        example(
            raw_message(
                "job-04",
                4,
                BOARD,
                "Your application was viewed by KESTREL ANALYTICS SDN. BHD.",
                f"Your application was viewed by KESTREL ANALYTICS SDN. BHD.\n\n{BOARD_FOOTER}",
                "<h1>Your application was viewed by KESTREL ANALYTICS SDN. BHD.</h1>"
                "<table><tr><td>Data Analyst</td><td>KESTREL ANALYTICS SDN. BHD.</td>"
                f"<td>Kuala Lumpur</td></tr></table><p>{BOARD_FOOTER}</p>",
            ),
            update("A01", "KESTREL ANALYTICS SDN. BHD.", "Data Analyst", "viewed"),
        ),
        example(
            raw_message(
                "job-05",
                5,
                "Lumen Works <jobs@lumen.example.invalid>",
                "Application received: Backend Engineer",
                "Thanks for applying to the Backend Engineer role at Lumen Works.",
            ),
            update("A02", "Lumen Works", "Backend Engineer", "applied"),
        ),
        example(
            raw_message(
                "job-06",
                6,
                "Lumen Works <jobs@lumen.example.invalid>",
                "Update on your Backend Engineer application",
                "Lumen Works will not be moving forward with your application for "
                "Backend Engineer.\n\nYou may also like: Backend Developer.",
            ),
            update("A02", "Lumen Works", "Backend Engineer", "rejected"),
        ),
        # The group name around the brand makes this a likely match for A03.
        example(
            raw_message(
                "job-07",
                7,
                "Orchid Harbour Group Recruiting <recruit@orchidgroup.example.invalid>",
                "Interview for Product Designer",
                "Orchid Harbour Group invites you to interview for Product Designer.",
            ),
            update("A03", "Orchid Harbour Group", "Product Designer", "interview"),
        ),
        # A re-application after the rejection reopens A02.
        example(
            raw_message(
                "job-08",
                8,
                "Lumen Works <jobs@lumen.example.invalid>",
                "Application received: Backend Engineer",
                "Thanks for applying again to the Backend Engineer role at Lumen Works.",
            ),
            update("A02", "Lumen Works", "Backend Engineer", "applied"),
        ),
        example(
            raw_message(
                "job-09",
                9,
                "Job Alerts <jobalerts@board.example.invalid>",
                "Data Analyst at Kestrel Analytics Sdn. Bhd.",
                "New jobs for you: Data Analyst at Kestrel Analytics Sdn. Bhd. and 4 more.",
            ),
            other("not_application"),
        ),
        example(
            raw_message(
                "job-10",
                10,
                "Nimbus Retail Talent <talent@nimbus.example.invalid>",
                "Opportunity: Cloud Architect at Nimbus Retail",
                "Hi Alex, we are hiring a Cloud Architect at Nimbus Retail. Interested?",
            ),
            other("not_application"),
        ),
        example(
            raw_message(
                "job-11",
                11,
                "Careers <no-reply@hiring.example.invalid>",
                "We received your application",
                "Thank you for your application. We will be in touch.",
            ),
            other("needs_review", ["company", "job_title"]),
        ),
        example(
            raw_message(
                "job-12",
                12,
                "Pinecrest Foods <careers@pinecrest.example.invalid>",
                "Application received",
                "Pinecrest Foods has received your application for Account Executive.",
            ),
            update("A04", "Pinecrest Foods", "Account Executive", "applied"),
        ),
        example(
            raw_message(
                "job-13",
                13,
                "Weekly Digest <news@digest.example.invalid>",
                "Your weekly reading list",
                "Five articles on balcony gardening.",
            ),
            other("not_application"),
        ),
        example(
            envelope(
                "syn-01",
                14,
                "Brightfield Systems <hr@brightfield.example.invalid>",
                "Application received: Platform Engineer",
                "Brightfield Systems has received your application for Platform Engineer.",
            ),
            update("S01", "Brightfield Systems", "Platform Engineer", "applied"),
            synthetic=True,
        ),
        example(
            envelope(
                "syn-02",
                15,
                "Brightfield Systems <hr@brightfield.example.invalid>",
                "Offer of employment: Platform Engineer",
                "Brightfield Systems is pleased to offer you the Platform Engineer position.",
            ),
            update("S01", "Brightfield Systems", "Platform Engineer", "offered"),
            synthetic=True,
        ),
        # A rejection after the offer is contradictory and stays unconfirmed.
        example(
            envelope(
                "syn-03",
                16,
                "Brightfield Systems <hr@brightfield.example.invalid>",
                "Update on your Platform Engineer application",
                "Brightfield Systems has decided not to proceed with your application for "
                "Platform Engineer.",
            ),
            update("S01", "Brightfield Systems", "Platform Engineer", "rejected"),
            synthetic=True,
        ),
    ]
    return {
        "dataset_version": "jobs-fictional-1",
        "contract_version": "jobs-v0",
        "examples": examples,
        "expected_applications": [
            {
                "key": "A01",
                "company": "Kestrel Analytics",
                "job_title": "Data Analyst",
                "status": "interview",
                "synthetic": False,
            },
            {
                "key": "A02",
                "company": "Lumen Works",
                "job_title": "Backend Engineer",
                "status": "applied",
                "synthetic": False,
            },
            {
                "key": "A03",
                "company": "Orchid Harbour",
                "job_title": "Product Designer",
                "status": "interview",
                "synthetic": False,
            },
            {
                "key": "A04",
                "company": "Pinecrest Foods",
                "job_title": "Account Executive",
                "status": "applied",
                "synthetic": False,
            },
            {
                "key": "S01",
                "company": "Brightfield Systems",
                "job_title": "Platform Engineer",
                "status": "offered",
                "synthetic": True,
            },
        ],
    }


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(json.dumps(dataset(), indent=2) + "\n", encoding="utf-8")
