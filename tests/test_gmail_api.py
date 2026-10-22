from __future__ import annotations

import base64
import json
import stat
from datetime import date, datetime
from types import SimpleNamespace

import httpx
import pytest
from google.oauth2.credentials import Credentials

from mizu_kakeibo import cli, gmail_api
from mizu_kakeibo.constants import (
    ANALYSIS_OUTPUT_CONTRACT,
    GMAIL_API_BASE_URL,
    GMAIL_READONLY_SCOPE,
)
from mizu_kakeibo.gmail_api import (
    GmailApiClient,
    GmailRetrievalError,
    GmailSignInError,
    load_credentials,
)
from mizu_kakeibo.models import AnalysisDecision, ExpenseAnalysisOutput, Transaction
from mizu_kakeibo.ollama import RuntimeMetadata

SNIPPET = "Fictional snippet text that must never be written"
RECEIVED = 1792166400000  # 2026-10-17T00:00:00+08:00


class FakeAnalyser:
    """Calls an expense for a receipt subject and a non-expense otherwise."""

    def __init__(self) -> None:
        self.inputs = []
        self.runtime_checks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def analyse(self, analyser_input):
        self.inputs.append(analyser_input)
        if "Receipt" in analyser_input.message.subject:
            return ExpenseAnalysisOutput(
                contract_version=ANALYSIS_OUTPUT_CONTRACT,
                decision=AnalysisDecision.EXPENSE,
                reason="Completed fictional purchase",
                transaction=Transaction(
                    merchant="Example Store",
                    amount_minor=4250,
                    currency="MYR",
                    transaction_date=date(2026, 10, 6),
                ),
                missing_fields=[],
            )
        return ExpenseAnalysisOutput(
            contract_version=ANALYSIS_OUTPUT_CONTRACT,
            decision=AnalysisDecision.NOT_EXPENSE,
            reason="No completed expense",
            transaction=None,
            missing_fields=[],
        )

    def verify_runtime(self):
        self.runtime_checks += 1
        return RuntimeMetadata(
            runtime="ollama",
            runtime_version="0.40.5",
            model="qwen3.5:4b-q4_K_M",
            model_digest="sha256-test",
            prompt_version="expense-v0.9",
            temperature=0,
            seed=42,
            context_size=12288,
            max_generated_tokens=512,
        )


def raw_rfc822(sender: str, subject: str) -> str:
    rfc822 = (
        f"From: {sender}\r\n"
        f"Subject: {subject}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "Purchase completed for MYR 42.50 on 2026-10-06.\r\n"
    )
    return base64.urlsafe_b64encode(rfc822.encode()).decode().rstrip("=")


def api_message(message_id: str, sender: str, subject: str, offset: int = 0) -> dict:
    return {
        "id": message_id,
        "threadId": message_id,
        "internalDate": str(RECEIVED + offset),
        "labelIds": ["INBOX", "UNREAD"],
        "sender": sender,
        "subject": subject,
    }


class FakeGmail:
    """A mocked Gmail API that also returns `snippet`, as the real API does."""

    def __init__(self, messages: list[dict], *, page_size: int = 2) -> None:
        self.messages = {message["id"]: message for message in messages}
        self.page_size = page_size
        self.requests: list[httpx.Request] = []
        self.raw_errors: dict[str, int] = {}
        self.status: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Authorization"] == "Bearer access-token"
        if self.status is not None:
            return httpx.Response(self.status, json={"error": {"code": self.status}})
        path = request.url.path.removeprefix("/gmail/v1/users/me/")
        params = request.url.params
        if path == "messages":
            ids = list(self.messages)
            start = int(params.get("pageToken", "0"))
            page: dict = {
                "messages": [
                    {"id": message_id, "threadId": message_id}
                    for message_id in ids[start : start + self.page_size]
                ],
                "resultSizeEstimate": len(ids),
            }
            if start + self.page_size < len(ids):
                page["nextPageToken"] = str(start + self.page_size)
            return httpx.Response(200, json=page)
        message = self.messages[path.removeprefix("messages/")]
        common = {
            "id": message["id"],
            "threadId": message["threadId"],
            "internalDate": message["internalDate"],
            "labelIds": message["labelIds"],
            "snippet": SNIPPET,
            "historyId": "1",
            "sizeEstimate": 100,
        }
        if params["format"] == "metadata":
            assert params["metadataHeaders"] == "From"
            return httpx.Response(
                200,
                json={
                    **common,
                    "payload": {"headers": [{"name": "From", "value": message["sender"]}]},
                },
            )
        assert params["format"] == "raw"
        status = self.raw_errors.get(message["id"])
        if status is not None:
            return httpx.Response(
                status,
                json={
                    "error": {
                        "code": status,
                        "status": "RESOURCE_EXHAUSTED",
                        "errors": [{"reason": "rateLimitExceeded"}],
                    }
                },
            )
        raw = message.get("raw") or raw_rfc822(message["sender"], message["subject"])
        return httpx.Response(200, json={**common, "raw": raw})

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url=GMAIL_API_BASE_URL, transport=httpx.MockTransport(self.handler)
        )

    def raw_requests(self) -> list[str]:
        return [
            request.url.path.rsplit("/", 1)[1]
            for request in self.requests
            if request.url.params.get("format") == "raw"
        ]


def token_info(*, expiry: str = "2099-01-01T00:00:00Z", scopes=None) -> dict:
    return {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id.apps.googleusercontent.com",
        "client_secret": "client-secret",
        "scopes": [GMAIL_READONLY_SCOPE] if scopes is None else scopes,
        "universe_domain": "googleapis.com",
        "account": "",
        "expiry": expiry,
    }


def write_token(directory, **kwargs) -> None:
    directory.mkdir(mode=0o700, exist_ok=True)
    (directory / gmail_api.TOKEN_FILE).write_text(json.dumps(token_info(**kwargs)))


class FakeTokenEndpoint:
    """Google's token endpoint, called by google-auth through its transport interface."""

    def __init__(self, status: int, body: dict) -> None:
        self.status = status
        self.body = body
        self.calls = 0

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **_):
        self.calls += 1
        return SimpleNamespace(status=self.status, data=json.dumps(self.body).encode(), headers={})


@pytest.fixture(autouse=True)
def no_google_token_requests(monkeypatch):
    """Fail any test that would refresh a token against Google itself."""

    def refuse():
        pytest.fail("a test tried to refresh a token through Google")

    monkeypatch.setattr(gmail_api, "_refresh_request", refuse)


def credentials() -> Credentials:
    return Credentials(token="access-token", scopes=[GMAIL_READONLY_SCOPE])


def test_search_follows_page_tokens_and_drops_duplicates() -> None:
    pages = {
        None: {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "page-2"},
        "page-2": {"messages": [{"id": "b"}, {"id": "c"}]},
    }
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        seen.append((params["q"], params.get("pageToken"), params["maxResults"]))
        return httpx.Response(200, json=pages[params.get("pageToken")])

    client = httpx.Client(base_url=GMAIL_API_BASE_URL, transport=httpx.MockTransport(handler))
    with GmailApiClient(credentials(), client=client) as gmail:
        assert gmail.list_message_ids("in:inbox after:2026/10/15") == ["a", "b", "c"]
    assert seen == [
        ("in:inbox after:2026/10/15", None, "500"),
        ("in:inbox after:2026/10/15", "page-2", "500"),
    ]


def test_a_search_that_matches_nothing_returns_no_ids() -> None:
    """With a `fields` mask, Gmail answers an empty search with 204 and no body."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["fields"])
        return httpx.Response(204)

    client = httpx.Client(base_url=GMAIL_API_BASE_URL, transport=httpx.MockTransport(handler))
    with GmailApiClient(credentials(), client=client) as gmail:
        assert gmail.list_message_ids("in:inbox after:2099/01/01") == []
        with pytest.raises(GmailRetrievalError, match="metadata for another message"):
            gmail.message_metadata("m1")
        with pytest.raises(GmailRetrievalError, match="no raw content"):
            gmail.raw_message("m1")
    assert seen[0] == "messages/id,nextPageToken"


def test_a_successful_response_that_is_not_json_is_a_retrieval_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    client = httpx.Client(base_url=GMAIL_API_BASE_URL, transport=httpx.MockTransport(handler))
    gmail = GmailApiClient(credentials(), client=client)
    with pytest.raises(GmailRetrievalError, match="invalid JSON"):
        gmail.list_message_ids("in:inbox")


def test_metadata_keeps_the_from_display_name_and_drops_the_snippet() -> None:
    fake = FakeGmail(
        [api_message("m1", "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "Jobs")]
    )
    with GmailApiClient(credentials(), client=fake.client()) as gmail:
        metadata = gmail.message_metadata("m1")
        raw = gmail.raw_message("m1")
    assert metadata == {
        "id": "m1",
        "threadId": "m1",
        "internalDate": str(RECEIVED),
        "labelIds": ["INBOX", "UNREAD"],
        "sender": "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
    }
    assert set(raw) == {"id", "threadId", "internalDate", "labelIds", "raw"}
    assert SNIPPET not in json.dumps([metadata, raw])
    assert "snippet" not in fake.requests[-1].url.params["fields"]


def test_an_api_refusal_of_the_sign_in_is_a_sign_in_error() -> None:
    fake = FakeGmail([])
    fake.status = 401
    gmail = GmailApiClient(credentials(), client=fake.client())
    with pytest.raises(GmailSignInError, match="gmail-sign-in"):
        gmail.list_message_ids("in:inbox")
    fake.status = 503
    gmail = GmailApiClient(credentials(), client=fake.client())
    with pytest.raises(GmailRetrievalError, match="HTTP 503"):
        gmail.list_message_ids("in:inbox")


def test_load_credentials_refreshes_an_expired_access_token_and_stores_it_privately(
    tmp_path,
) -> None:
    directory = tmp_path / "gmail-api"
    write_token(directory, expiry="2026-01-01T00:00:00Z")
    directory.chmod(0o755)
    endpoint = FakeTokenEndpoint(
        200,
        {
            "access_token": "fresh-access-token",
            "expires_in": 3600,
            "scope": GMAIL_READONLY_SCOPE,
            "token_type": "Bearer",
        },
    )
    loaded = load_credentials(directory, request=endpoint)
    assert loaded.token == "fresh-access-token"
    assert endpoint.calls == 1
    token = directory / gmail_api.TOKEN_FILE
    assert json.loads(token.read_text())["token"] == "fresh-access-token"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700

    # A token that is still valid is used without contacting Google.
    assert load_credentials(directory, request=endpoint).token == "fresh-access-token"
    assert endpoint.calls == 1


def test_load_credentials_rejects_a_missing_expired_or_wider_sign_in(tmp_path) -> None:
    directory = tmp_path / "gmail-api"
    with pytest.raises(GmailSignInError, match="not signed in"):
        load_credentials(directory)

    write_token(directory, scopes=[GMAIL_READONLY_SCOPE, "https://mail.google.com/"])
    with pytest.raises(GmailSignInError, match="gmail.readonly"):
        load_credentials(directory)

    write_token(directory, expiry="2026-01-01T00:00:00Z")
    endpoint = FakeTokenEndpoint(
        400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
    )
    with pytest.raises(GmailSignInError, match="expired or been removed"):
        load_credentials(directory, request=endpoint)
    assert json.loads((directory / gmail_api.TOKEN_FILE).read_text())["token"] == "access-token"


@pytest.fixture
def sync_setup(tmp_path, monkeypatch):
    directory = tmp_path / "gmail-api"
    write_token(directory)
    storage = tmp_path / "live"
    storage.mkdir()
    analyser = FakeAnalyser()
    fake = FakeGmail(
        [
            api_message("m1", "Example Store <receipts@example.invalid>", "Receipt"),
            api_message(
                "m2", "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "Jobs", offset=1
            ),
            api_message("m3", "LinkedIn <messages-noreply@linkedin.com>", "Hello", offset=2),
        ]
    )
    monkeypatch.setattr(cli, "OllamaAnalyser", lambda: analyser)
    monkeypatch.setattr(
        cli, "GmailApiClient", lambda creds: GmailApiClient(creds, client=fake.client())
    )
    argv = [
        "sync",
        "--gmail-directory",
        str(directory),
        "--database",
        str(storage / "kakeibo.sqlite3"),
        "--ledger",
        str(storage / "ledger.csv"),
    ]
    return SimpleNamespace(
        fake=fake, analyser=analyser, argv=argv, storage=storage, directory=directory
    )


def test_sync_retrieves_filters_and_processes_new_messages_then_skips_them(
    sync_setup, capsys
) -> None:
    assert cli.main(sync_setup.argv) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["summary"] == {
        "expense": ["m1"],
        "not_expense": ["m3"],
        "needs_review": [],
        "skipped": [],
        "filtered": ["m2"],
        "failed": [],
        "deferred": [],
    }
    searches = [r for r in sync_setup.fake.requests if r.url.path.endswith("/messages")]
    assert searches[0].url.params["q"] == (
        "in:inbox -category:promotions -category:social after:2026/10/15"
    )
    assert len(searches) == 2  # three results over two pages
    assert sync_setup.fake.raw_requests() == ["m1", "m3"]
    assert (sync_setup.storage / "ledger.csv").read_text().splitlines()[1] == (
        "gmail,m1,Example Store,4250,MYR,2026-10-06"
    )
    assert SNIPPET not in output
    for path in sync_setup.storage.iterdir():
        assert SNIPPET.encode() not in path.read_bytes()

    sync_setup.fake.requests.clear()
    assert cli.main(sync_setup.argv) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["summary"]["skipped"] == ["m1", "m3"]
    assert replay["summary"]["filtered"] == ["m2"]
    assert sync_setup.fake.raw_requests() == []
    assert len(sync_setup.analyser.inputs) == 2


def test_sync_records_and_retrieves_nothing_while_another_run_holds_the_lock(
    sync_setup, capsys, hold_run_lock
) -> None:
    holder = hold_run_lock(sync_setup.storage / "kakeibo.sqlite3")

    assert cli.main(sync_setup.argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "locked"
    assert report["holder"]["pid"] == holder.pid
    assert sync_setup.fake.requests == []
    assert sync_setup.analyser.inputs == []
    assert sorted(path.name for path in sync_setup.storage.iterdir()) == ["kakeibo.sqlite3.lock"]


def test_sync_applies_the_batch_limit_and_defers_the_rest(sync_setup, capsys) -> None:
    assert cli.main([*sync_setup.argv, "--limit", "1"]) == 0
    summary = json.loads(capsys.readouterr().out)["summary"]
    assert summary["expense"] == ["m1"]
    assert summary["deferred"] == ["m3"]
    assert sync_setup.fake.raw_requests() == ["m1"]


def test_a_rate_limited_message_fails_and_is_retried_by_the_next_sync(sync_setup, capsys) -> None:
    sync_setup.fake.raw_errors["m1"] = 429
    assert cli.main(sync_setup.argv) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["failed"] == ["m1"]
    assert report["summary"]["not_expense"] == ["m3"]
    failed = next(result for result in report["results"] if result["message_id"] == "m1")
    assert failed["error"] == "Gmail retrieval error: Gmail API returned HTTP 429 rateLimitExceeded"
    assert len(sync_setup.analyser.inputs) == 1

    sync_setup.fake.raw_errors.clear()
    assert cli.main([*sync_setup.argv, "--full"]) == 0
    retried = json.loads(capsys.readouterr().out)
    assert retried["summary"]["expense"] == ["m1"]
    assert retried["summary"]["skipped"] == ["m3"]


def test_a_message_that_cannot_be_normalized_fails_and_stays_retryable(sync_setup, capsys) -> None:
    sync_setup.fake.messages["m1"]["raw"] = "A"  # one character short of valid base64url
    assert cli.main(sync_setup.argv) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["failed"] == ["m1"]
    assert report["summary"]["not_expense"] == ["m3"]
    assert report["results"][0]["error"].startswith("normalization failed")

    del sync_setup.fake.messages["m1"]["raw"]
    assert cli.main([*sync_setup.argv, "--full"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["expense"] == ["m1"]


def test_an_expired_sign_in_stops_the_sync_before_retrieval(
    sync_setup, monkeypatch, capsys
) -> None:
    write_token(sync_setup.directory, expiry="2026-01-01T00:00:00Z")
    endpoint = FakeTokenEndpoint(400, {"error": "invalid_grant"})
    monkeypatch.setattr(gmail_api, "_refresh_request", lambda: endpoint)
    with pytest.raises(SystemExit) as stopped:
        cli.main(sync_setup.argv)
    assert stopped.value.code == 2
    assert "gmail-sign-in" in capsys.readouterr().err
    assert endpoint.calls == 1
    assert sync_setup.fake.requests == []
    assert sync_setup.analyser.inputs == []
    assert sync_setup.analyser.runtime_checks == 0
    assert not (sync_setup.storage / "ledger.csv").exists()


def test_a_sign_in_refused_during_retrieval_records_nothing(sync_setup, capsys) -> None:
    sync_setup.fake.status = 401
    with pytest.raises(SystemExit) as stopped:
        cli.main(sync_setup.argv)
    assert stopped.value.code == 2
    assert "gmail-sign-in" in capsys.readouterr().err
    assert sync_setup.analyser.inputs == []
    assert len((sync_setup.storage / "ledger.csv").read_text().splitlines()) == 1


def test_sync_matches_the_local_batch_command(sync_setup, tmp_path, capsys) -> None:
    assert cli.main(sync_setup.argv) == 0
    synced = json.loads(capsys.readouterr().out)
    sync_inputs = list(sync_setup.analyser.inputs)

    with GmailApiClient(credentials(), client=sync_setup.fake.client()) as gmail:
        raw_messages = [gmail.raw_message(message_id) for message_id in ("m1", "m3")]
    batch = tmp_path / "messages.json"
    batch.write_text(json.dumps(raw_messages))
    local = tmp_path / "local"
    local.mkdir()
    sync_setup.analyser.inputs.clear()
    assert (
        cli.main(
            [
                "process",
                str(batch),
                "--database",
                str(local / "kakeibo.sqlite3"),
                "--ledger",
                str(local / "ledger.csv"),
            ]
        )
        == 0
    )
    processed = json.loads(capsys.readouterr().out)
    assert sync_setup.analyser.inputs == sync_inputs
    assert processed["results"] == synced["results"]
    assert (local / "ledger.csv").read_text() == (sync_setup.storage / "ledger.csv").read_text()


class FakeFlow:
    granted = [GMAIL_READONLY_SCOPE]
    runs: list[dict] = []

    @classmethod
    def from_client_secrets_file(cls, path, scopes):
        assert scopes == [GMAIL_READONLY_SCOPE]
        return cls()

    def run_local_server(self, **kwargs):
        FakeFlow.runs.append(kwargs)
        return Credentials(
            token="access-token",
            refresh_token="refresh-token",
            token_uri="https://oauth2.googleapis.com/token",
            client_id="client-id.apps.googleusercontent.com",
            client_secret="client-secret",
            scopes=[GMAIL_READONLY_SCOPE],
            granted_scopes=FakeFlow.granted,
            expiry=datetime(2099, 1, 1),
        )


@pytest.fixture
def sign_in_directory(tmp_path, monkeypatch):
    FakeFlow.runs = []
    FakeFlow.granted = [GMAIL_READONLY_SCOPE]
    monkeypatch.setattr(gmail_api, "InstalledAppFlow", FakeFlow)
    directory = tmp_path / "gmail-api"
    directory.mkdir()
    (directory / gmail_api.CLIENT_SECRET_FILE).write_text("{}")
    return directory


def test_sign_in_stores_a_private_read_only_token(sign_in_directory, capsys) -> None:
    assert cli.main(["gmail-sign-in", "--gmail-directory", str(sign_in_directory)]) == 0
    assert json.loads(capsys.readouterr().out)["scopes"] == [GMAIL_READONLY_SCOPE]
    assert FakeFlow.runs == [
        {"host": "localhost", "port": 0, "open_browser": True, "prompt": "consent"}
    ]
    token = sign_in_directory / gmail_api.TOKEN_FILE
    issued = sign_in_directory / gmail_api.ISSUED_FILE
    assert json.loads(token.read_text())["scopes"] == [GMAIL_READONLY_SCOPE]
    assert issued.read_text().startswith("20")
    for path in (token, issued, sign_in_directory / gmail_api.CLIENT_SECRET_FILE):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(sign_in_directory.stat().st_mode) == 0o700

    # A working sign-in is kept unless the user asks to replace it.
    with pytest.raises(SystemExit):
        cli.main(["gmail-sign-in", "--gmail-directory", str(sign_in_directory)])
    assert "--replace" in capsys.readouterr().err
    assert len(FakeFlow.runs) == 1
    assert (
        cli.main(["gmail-sign-in", "--gmail-directory", str(sign_in_directory), "--replace"]) == 0
    )
    assert len(FakeFlow.runs) == 2


def test_sign_in_stores_nothing_when_more_than_read_only_is_granted(sign_in_directory) -> None:
    FakeFlow.granted = [GMAIL_READONLY_SCOPE, "https://mail.google.com/"]
    with pytest.raises(GmailSignInError, match="nothing was stored"):
        gmail_api.sign_in(sign_in_directory)
    assert not (sign_in_directory / gmail_api.TOKEN_FILE).exists()


def test_sign_in_needs_the_oauth_client(tmp_path) -> None:
    with pytest.raises(GmailSignInError, match="Desktop client"):
        gmail_api.sign_in(tmp_path / "gmail-api")
