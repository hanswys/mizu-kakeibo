"""`jobs-sync` on a mocked Gmail API and a scripted Ollama, with fictional messages only."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from jobs_fakes import ScriptedOllama, other, update

from mizu_kakeibo import cli, gmail_api
from mizu_kakeibo.constants import GMAIL_API_BASE_URL, GMAIL_READONLY_SCOPE
from mizu_kakeibo.gmail_api import GmailApiClient
from mizu_kakeibo.jobs.constants import JOBS_RECEIVED_SINCE
from mizu_kakeibo.jobs.storage import JobStore
from mizu_kakeibo.selection import search_query, select_new_messages

START = datetime.fromisoformat(JOBS_RECEIVED_SINCE)
LINKEDIN = "LinkedIn <jobs-noreply@linkedin.com>"
CAREERS = "Example Careers <careers@example.invalid>"
DIGEST = "Example Digest <digest@example.invalid>"
QUERY = f"in:inbox after:{int(START.timestamp()) - 1}"

ANSWERS = {
    "Your application was sent": update("Example Company", "Software Engineer", "applied"),
    "Interview invitation": update("Example Company", "Software Engineer", "interview"),
    "Your application digest": other("not_application"),
}


def at(day: int, hours: float = 10) -> int:
    """Milliseconds since the epoch, `day` days after the first-run start, Malaysia time."""
    return int((START + timedelta(days=day, hours=hours)).timestamp() * 1000)


def raw_alternative(sender: str, subject: str, plain: str, html: str) -> str:
    rfc822 = (
        f"From: {sender}\r\n"
        f"Subject: {subject}\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/alternative; boundary="part"\r\n\r\n'
        "--part\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        f"{plain}\r\n"
        "--part\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        f"{html}\r\n"
        "--part--\r\n"
    )
    return base64.urlsafe_b64encode(rfc822.encode()).decode().rstrip("=")


def gmail_message(message_id, received, sender, subject, *, labels=("INBOX",)) -> dict:
    # The job title is only in the HTML alternative, as in some LinkedIn notifications.
    plain = f"An update from {sender}: see the HTML version."
    html = (
        f"<p>{subject}.</p><p>Example Company - Software Engineer: "
        "your application has an update.</p>"
    )
    return {
        "id": message_id,
        "threadId": f"t-{message_id}",
        "internalDate": str(received),
        "labelIds": list(labels),
        "sender": sender,
        "raw": raw_alternative(sender, subject, plain, html),
    }


class FakeGmail:
    """A mocked Gmail API whose search lists newest first and can refuse requests.

    A search applies `in:inbox` and epoch-second `after:` and `before:` terms, and answers
    HTTP 204 with no body when nothing matches, as Gmail does under a `fields` mask.
    `refusals` maps `list`, `metadata:<id>`, or `raw:<id>` to the responses to send, one per
    request, before answering normally.
    """

    def __init__(self, messages: list[dict], *, page_size: int = 2) -> None:
        self.messages = {message["id"]: message for message in messages}
        self.page_size = page_size
        self.requests: list[str] = []
        self.queries: list[str] = []
        self.refusals: dict[str, list[httpx.Response]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer access-token"
        path = request.url.path.removeprefix("/gmail/v1/users/me/")
        params = request.url.params
        if path == "messages":
            key = "list"
            self.queries.append(params["q"])
        else:
            key = f"{params['format']}:{path.removeprefix('messages/')}"
        self.requests.append(key)
        if self.refusals.get(key):
            return self.refusals[key].pop(0)
        if key == "list":
            ids = [
                message_id
                for message_id, message in sorted(
                    self.messages.items(), key=lambda item: -int(item[1]["internalDate"])
                )
                if matches(params["q"], message)
            ]
            if not ids:
                return httpx.Response(204)
            start = int(params.get("pageToken", "0"))
            page: dict = {"messages": [{"id": i} for i in ids[start : start + self.page_size]]}
            if start + self.page_size < len(ids):
                page["nextPageToken"] = str(start + self.page_size)
            return httpx.Response(200, json=page)
        message = self.messages[key.partition(":")[2]]
        common = {
            "id": message["id"],
            "threadId": message["threadId"],
            "internalDate": message["internalDate"],
            "labelIds": message["labelIds"],
            "snippet": "Fictional snippet",
        }
        if params["format"] == "metadata":
            headers = [{"name": "From", "value": message["sender"]}]
            return httpx.Response(200, json={**common, "payload": {"headers": headers}})
        return httpx.Response(200, json={**common, "raw": message["raw"]})

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url=GMAIL_API_BASE_URL, transport=httpx.MockTransport(self.handler)
        )

    def of(self, kind: str) -> list[str]:
        return [key.partition(":")[2] for key in self.requests if key.startswith(f"{kind}:")]


def matches(query: str, message: dict) -> bool:
    seconds = int(message["internalDate"]) // 1000
    for term in query.split():
        name, _, value = term.partition(":")
        if term == "in:inbox" and "INBOX" not in message["labelIds"]:
            return False
        if name == "after" and value.isdigit() and seconds <= int(value):
            return False
        if name == "before" and value.isdigit() and seconds >= int(value):
            return False
    return True


def refusal(status: int = 429, reason: str = "rateLimitExceeded"):
    body = {"error": {"code": status, "errors": [{"reason": reason}]}}
    return httpx.Response(status, json=body)


def token(directory) -> None:
    directory.mkdir(mode=0o700)
    (directory / gmail_api.TOKEN_FILE).write_text(
        json.dumps(
            {
                "token": "access-token",
                "refresh_token": "refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "client-id.apps.googleusercontent.com",
                "client_secret": "client-secret",
                "scopes": [GMAIL_READONLY_SCOPE],
                "expiry": "2099-01-01T00:00:00Z",
            }
        )
    )


@pytest.fixture
def jobs_sync(tmp_path, monkeypatch):
    fake = FakeGmail(
        [
            gmail_message(
                "j1",
                at(1),
                LINKEDIN,
                "Your application was sent",
                labels=("INBOX", "CATEGORY_SOCIAL"),
            ),
            gmail_message("j2", at(3), CAREERS, "Interview invitation"),
            gmail_message("j3", at(5), DIGEST, "Your application digest"),
            # The day before the start in Malaysia time, inside a date search's extra day.
            gmail_message("early", at(0, hours=-2), CAREERS, "Interview invitation"),
            gmail_message("archived", at(4), CAREERS, "Interview invitation", labels=()),
        ]
    )
    ollama = ScriptedOllama(ANSWERS)
    monkeypatch.setattr(cli, "OllamaJobAnalyser", ollama.analyser)
    monkeypatch.setattr(
        cli,
        "GmailApiClient",
        lambda creds: GmailApiClient(creds, client=fake.client()),
    )
    directory = tmp_path / "gmail-api"
    token(directory)
    database = tmp_path / "jobs" / "job-tracker.sqlite3"
    argv = ["jobs-sync", "--gmail-directory", str(directory), "--database", str(database)]
    return SimpleNamespace(
        fake=fake, ollama=ollama, argv=argv, database=database, directory=directory
    )


def run(setup, capsys, *extra: str) -> tuple[int, dict]:
    code = cli.main([*setup.argv, *extra])
    return code, json.loads(capsys.readouterr().out)


def test_jobs_sync_processes_every_new_inbox_message_oldest_first(jobs_sync, capsys) -> None:
    code, report = run(jobs_sync, capsys)

    assert code == 0
    assert jobs_sync.fake.queries == [QUERY] * 2  # three IDs, two per page
    assert report["summary"] == {
        "status_update": ["j1", "j2"],
        "not_application": ["j3"],
        "review": [],
        "skipped": [],
        "failed": [],
        "deferred": [],
    }
    assert report["already_processed"] == 0
    assert report["applications"] == {"created": 1, "advanced": 1, "reopened": 0, "unchanged": 0}
    assert report["skim"] == [
        {"message_id": "j3", "sender": DIGEST, "subject": "Your application digest"}
    ]
    # No skip filter: a social-category LinkedIn status email is processed. The exact start
    # leaves out the message from the day before, so it is never requested.
    assert jobs_sync.fake.of("metadata") == ["j3", "j2", "j1"]
    assert jobs_sync.fake.of("raw") == ["j1", "j2", "j3"]
    stored = JobStore(jobs_sync.database).message("gmail", "j1")
    assert stored.content.source_type == "html_converted"
    assert "Software Engineer" in stored.content.text
    for path in jobs_sync.database.parent.iterdir():
        assert b"Fictional snippet" not in path.read_bytes()


def test_a_replay_requests_no_metadata_for_processed_messages(jobs_sync, capsys) -> None:
    run(jobs_sync, capsys)
    jobs_sync.fake.requests.clear()
    jobs_sync.fake.queries.clear()

    code, report = run(jobs_sync, capsys)

    assert code == 0
    assert jobs_sync.fake.queries == [QUERY] * 2
    assert jobs_sync.fake.of("metadata") == []
    assert jobs_sync.fake.of("raw") == []
    assert report["already_processed"] == 3
    assert all(ids == [] for ids in report["summary"].values())
    assert len(jobs_sync.ollama.calls) == 3


def test_the_limit_defers_newer_messages_to_the_next_sync(jobs_sync, capsys) -> None:
    code, report = run(jobs_sync, capsys, "--limit", "1")
    assert code == 0
    assert report["summary"]["status_update"] == ["j1"]
    assert report["summary"]["deferred"] == ["j2", "j3"]

    code, report = run(jobs_sync, capsys, "--limit", "1")
    assert report["summary"]["status_update"] == ["j2"]
    assert report["summary"]["deferred"] == ["j3"]
    assert report["already_processed"] == 1
    assert jobs_sync.fake.of("raw") == ["j1", "j2"]


def before(moment: datetime) -> str:
    return f"{QUERY} before:{int(moment.timestamp())}"


def test_a_backlog_run_requests_metadata_only_for_the_oldest_window(jobs_sync, capsys) -> None:
    code, report = run(jobs_sync, capsys, "--limit", "1")
    assert code == 0
    assert report["summary"]["status_update"] == ["j1"]
    assert report["summary"]["deferred"] == ["j2", "j3"]
    assert jobs_sync.fake.of("metadata") == ["j1"]
    # The first one-day window holds nothing, which Gmail answers with no body.
    assert jobs_sync.fake.queries[2:] == [
        before(START + timedelta(days=1)),
        before(START + timedelta(days=2)),
    ]

    jobs_sync.fake.queries.clear()
    code, report = run(jobs_sync, capsys, "--limit", "1")
    assert report["summary"]["status_update"] == ["j2"]
    assert report["summary"]["deferred"] == ["j3"]
    assert jobs_sync.fake.of("metadata") == ["j1", "j2"]
    # Windows now end one, two, then four days after the newest processed message.
    j1_received = START + timedelta(days=1, hours=10)
    assert jobs_sync.fake.queries[2:] == [
        before(j1_received + timedelta(days=days)) for days in (1, 2, 4)
    ]


def test_a_failed_message_older_than_the_newest_processed_is_selected_first(
    jobs_sync, capsys
) -> None:
    jobs_sync.fake.refusals["raw:j1"] = [httpx.Response(500, json={"error": {"code": 500}})]
    code, report = run(jobs_sync, capsys, "--limit", "2")
    assert report["summary"]["failed"] == ["j1"]
    assert report["summary"]["status_update"] == ["j2"]

    code, report = run(jobs_sync, capsys, "--limit", "1")
    assert code == 0
    assert report["summary"]["status_update"] == ["j1"]
    assert report["summary"]["deferred"] == ["j3"]


def test_an_empty_inbox_syncs_nothing(jobs_sync, capsys) -> None:
    jobs_sync.fake.messages.clear()
    code, report = run(jobs_sync, capsys)
    assert code == 0
    assert report["already_processed"] == 0
    assert all(ids == [] for ids in report["summary"].values())


def test_a_message_that_failed_before_newer_ones_is_found_by_the_next_sync(
    jobs_sync, capsys
) -> None:
    jobs_sync.fake.refusals["raw:j1"] = [httpx.Response(500, json={"error": {"code": 500}})]
    code, report = run(jobs_sync, capsys)
    assert code == 1
    assert report["summary"]["failed"] == ["j1"]
    assert report["summary"]["status_update"] == ["j2"]
    failed = next(result for result in report["results"] if result["message_id"] == "j1")
    assert failed["error"] == "Gmail retrieval error: Gmail API returned HTTP 500"

    # The search never starts at the newest processed message, so j1 is retried without --full.
    code, report = run(jobs_sync, capsys)
    assert code == 0
    assert report["summary"]["status_update"] == ["j1"]
    assert report["already_processed"] == 2


def test_a_message_that_cannot_be_normalized_fails_and_stays_retryable(jobs_sync, capsys) -> None:
    good = jobs_sync.fake.messages["j2"]["raw"]
    jobs_sync.fake.messages["j2"]["raw"] = "A"  # one character short of valid base64url
    code, report = run(jobs_sync, capsys)
    assert code == 1
    assert report["summary"]["failed"] == ["j2"]
    assert report["results"][1]["error"].startswith("normalization failed")

    jobs_sync.fake.messages["j2"]["raw"] = good
    code, report = run(jobs_sync, capsys)
    assert code == 0
    assert report["summary"]["status_update"] == ["j2"]


def test_a_rate_limited_metadata_request_ends_the_run_and_records_nothing(
    jobs_sync, capsys
) -> None:
    jobs_sync.fake.refusals["metadata:j2"] = [refusal()]
    with pytest.raises(SystemExit) as stopped:
        cli.main(jobs_sync.argv)
    assert stopped.value.code == 2
    assert "HTTP 429 rateLimitExceeded" in capsys.readouterr().err
    assert jobs_sync.fake.of("raw") == []
    assert jobs_sync.ollama.calls == []
    assert JobStore(jobs_sync.database).filter_new_ids("gmail", ["j1", "j3"]) == ["j1", "j3"]


def test_a_rate_limited_fetch_fails_that_message_alone(jobs_sync, capsys) -> None:
    jobs_sync.fake.refusals["raw:j1"] = [refusal()]
    code, report = run(jobs_sync, capsys)
    assert code == 1
    assert report["summary"]["failed"] == ["j1"]
    assert report["summary"]["status_update"] == ["j2"]
    failed = report["results"][0]
    assert failed["error"] == "Gmail retrieval error: Gmail API returned HTTP 429 rateLimitExceeded"


def test_jobs_sync_records_and_retrieves_nothing_while_another_job_run_holds_the_lock(
    jobs_sync, capsys, hold_run_lock
) -> None:
    holder = hold_run_lock(jobs_sync.database)
    code, report = run(jobs_sync, capsys)
    assert code == 0
    assert report["status"] == "locked"
    assert report["holder"]["pid"] == holder.pid
    assert jobs_sync.fake.requests == []
    assert jobs_sync.ollama.calls == []
    assert not jobs_sync.database.exists()


def test_a_missing_sign_in_stops_jobs_sync_before_gmail_or_the_runtime(jobs_sync, capsys) -> None:
    (jobs_sync.directory / gmail_api.TOKEN_FILE).unlink()
    with pytest.raises(SystemExit) as stopped:
        cli.main(jobs_sync.argv)
    assert stopped.value.code == 2
    assert "gmail-sign-in" in capsys.readouterr().err
    assert jobs_sync.fake.requests == []
    assert not jobs_sync.database.exists()


def test_selection_without_a_skip_filter_keeps_categories_and_job_senders() -> None:
    metadata = {
        "social": {"internalDate": str(at(1)), "labelIds": ["INBOX", "CATEGORY_SOCIAL"]},
        "alert": {"internalDate": str(at(2)), "labelIds": ["INBOX"], "sender": LINKEDIN},
    }
    kept = select_new_messages(
        metadata, [], lambda ids: ids, received_since=START, limit=25, skip_filter=None
    )
    filtered = select_new_messages(metadata, [], lambda ids: ids, received_since=START, limit=25)
    assert kept.message_ids == ["social", "alert"]
    assert kept.filtered_ids == []
    assert filtered.filtered_ids == ["alert", "social"]


def test_the_search_leaves_out_no_categories_when_given_no_terms() -> None:
    assert search_query(START, None, skipped_terms="") == "in:inbox after:2026/10/07"
    assert search_query(START, None) == (
        "in:inbox -category:promotions -category:social after:2026/10/07"
    )


def test_the_job_store_reports_new_ids_and_the_newest_processed_message(jobs_sync, capsys) -> None:
    store = JobStore(jobs_sync.database)
    assert store.filter_new_ids("gmail", ["j2", "j1", "j2"]) == ["j2", "j1"]
    assert store.latest_received_at("gmail") is None

    run(jobs_sync, capsys, "--limit", "2")

    assert store.filter_new_ids("gmail", ["j3", "j2", "j1", "x"]) == ["j3", "x"]
    assert store.latest_received_at("gmail") == datetime.fromtimestamp(at(3) / 1000, timezone.utc)
