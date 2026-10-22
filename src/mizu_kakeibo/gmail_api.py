from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .constants import (
    GMAIL_API_BASE_URL,
    GMAIL_READONLY_SCOPE,
    GMAIL_REQUEST_TIMEOUT_SECONDS,
    SKIPPED_SEARCH_TERMS,
)
from .selection import Selection, is_filtered, search_query, select_new_messages

GMAIL_API_DIRECTORY = Path.home() / "mizu-kakeibo-private" / "gmail-api"
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token-readonly.json"
ISSUED_FILE = "token-readonly.issued"

SIGN_IN_COMMAND = "mizu-kakeibo gmail-sign-in"
_RAW_FIELDS = ("id", "threadId", "internalDate", "labelIds", "raw")


class GmailSignInError(RuntimeError):
    """The Gmail sign-in is missing, expired, removed, or grants the wrong scope."""


class GmailRetrievalError(RuntimeError):
    """A Gmail API request failed for a reason other than the sign-in."""


def _write_private(path: Path, text: str) -> None:
    """Replace a file with one readable only by its owner, in a directory only they can open."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        target.write(text)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _refresh_request() -> Request:
    from google.auth.transport.requests import Request as RequestsRequest

    return RequestsRequest()


def load_credentials(
    directory: Path = GMAIL_API_DIRECTORY, *, request: Request | None = None
) -> Credentials:
    """Read the stored sign-in, refreshing and rewriting it when its access token has expired.

    Never opens a browser. A missing, unreadable, expired, or removed sign-in, or one that
    grants anything other than `gmail.readonly`, raises GmailSignInError.
    """
    path = directory / TOKEN_FILE
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise GmailSignInError(f"Gmail is not signed in; run `{SIGN_IN_COMMAND}`") from error
    except (OSError, json.JSONDecodeError) as error:
        raise GmailSignInError(
            f"cannot read the Gmail sign-in at {path}; run `{SIGN_IN_COMMAND} --replace`"
        ) from error
    if (
        not isinstance(info, dict)
        or info.get("scopes") != [GMAIL_READONLY_SCOPE]
        or not info.get("refresh_token")
    ):
        raise GmailSignInError(
            "the Gmail sign-in is not a gmail.readonly sign-in with a refresh token; "
            f"run `{SIGN_IN_COMMAND} --replace`"
        )
    try:
        credentials = Credentials.from_authorized_user_info(info, [GMAIL_READONLY_SCOPE])
    except ValueError as error:
        raise GmailSignInError(
            f"the Gmail sign-in is malformed; run `{SIGN_IN_COMMAND} --replace`"
        ) from error
    if credentials.valid:
        return credentials
    try:
        credentials.refresh(request or _refresh_request())
    except RefreshError as error:
        if getattr(error, "retryable", False):
            raise GmailRetrievalError("Google could not refresh the Gmail sign-in") from error
        raise GmailSignInError(
            f"the Gmail sign-in has expired or been removed; run `{SIGN_IN_COMMAND} --replace`"
        ) from error
    except TransportError as error:
        raise GmailRetrievalError("cannot reach Google to refresh the Gmail sign-in") from error
    _write_private(path, credentials.to_json())
    return credentials


def sign_in(directory: Path = GMAIL_API_DIRECTORY) -> Credentials:
    """Run the browser consent through a loopback redirect and store the resulting token."""
    client_secret = directory / CLIENT_SECRET_FILE
    if not client_secret.is_file():
        raise GmailSignInError(
            f"no OAuth client at {client_secret}; save the Desktop client's JSON there"
        )
    os.chmod(directory, 0o700)
    os.chmod(client_secret, 0o600)
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret), scopes=[GMAIL_READONLY_SCOPE]
    )
    credentials = flow.run_local_server(
        host="localhost", port=0, open_browser=True, prompt="consent"
    )
    granted = set(credentials.granted_scopes or credentials.scopes or ())
    if granted != {GMAIL_READONLY_SCOPE} or not credentials.refresh_token:
        raise GmailSignInError(
            "Google did not grant gmail.readonly alone with a refresh token; nothing was stored"
        )
    _write_private(directory / TOKEN_FILE, credentials.to_json())
    _write_private(directory / ISSUED_FILE, datetime.now(timezone.utc).isoformat() + "\n")
    return credentials


def _error_reason(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        reason = error.get("errors", [{}])[0].get("reason") or error.get("status")
    except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
        reason = None
    return f"HTTP {response.status_code}" + (f" {reason}" if isinstance(reason, str) else "")


class GmailApiClient:
    """Read-only Gmail API calls for one sync. Responses never carry `snippet` out."""

    def __init__(self, credentials: Credentials, *, client: httpx.Client | None = None) -> None:
        self._credentials = credentials
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=GMAIL_API_BASE_URL, timeout=GMAIL_REQUEST_TIMEOUT_SECONDS
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GmailApiClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {self._credentials.token}"},
            )
        except httpx.HTTPError as error:
            raise GmailRetrievalError(
                f"Gmail API request failed: {type(error).__name__}"
            ) from error
        if response.status_code == 401:
            raise GmailSignInError(f"Gmail refused the sign-in; run `{SIGN_IN_COMMAND} --replace`")
        if response.is_error:
            raise GmailRetrievalError(f"Gmail API returned {_error_reason(response)}")
        if response.status_code == 204:
            return {}  # a search with a `fields` mask that matches nothing has no body
        try:
            value = response.json()
        except json.JSONDecodeError as error:
            raise GmailRetrievalError("Gmail API returned invalid JSON") from error
        if not isinstance(value, dict):
            raise GmailRetrievalError("Gmail API returned an unexpected response")
        return value

    def list_message_ids(self, query: str) -> list[str]:
        """Return the IDs of every message matching a search, following page tokens to the end."""
        message_ids: list[str] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "q": query,
                "maxResults": 500,
                "fields": "messages/id,nextPageToken",
            }
            if page_token:
                params["pageToken"] = page_token
            page = self._get("messages", params)
            for message in page.get("messages") or []:
                if isinstance(message, dict) and isinstance(message.get("id"), str):
                    message_ids.append(message["id"])
            page_token = page.get("nextPageToken")
            if not isinstance(page_token, str) or not page_token:
                return list(dict.fromkeys(message_ids))

    def message_metadata(self, message_id: str) -> dict[str, Any]:
        """Return selection metadata, with the `From` header as `sender`."""
        value = self._get(
            f"messages/{quote(message_id, safe='')}",
            {
                "format": "metadata",
                "metadataHeaders": "From",
                "fields": "id,threadId,internalDate,labelIds,payload/headers",
            },
        )
        if value.get("id") != message_id:
            raise GmailRetrievalError("Gmail API returned metadata for another message")
        headers = (value.get("payload") or {}).get("headers") or []
        sender = next(
            (
                header.get("value")
                for header in headers
                if isinstance(header, dict) and str(header.get("name", "")).lower() == "from"
            ),
            None,
        )
        return {
            "id": message_id,
            "threadId": value.get("threadId"),
            "internalDate": value.get("internalDate"),
            "labelIds": value.get("labelIds"),
            "sender": sender,
        }

    def raw_message(self, message_id: str) -> dict[str, Any]:
        """Return a message in the connector's `RAW` shape, with `snippet` removed."""
        value = self._get(
            f"messages/{quote(message_id, safe='')}",
            {"format": "raw", "fields": ",".join(_RAW_FIELDS)},
        )
        value.pop("snippet", None)
        if value.get("id") != message_id or not isinstance(value.get("raw"), str):
            raise GmailRetrievalError("Gmail API returned no raw content for the message")
        return {field: value[field] for field in _RAW_FIELDS if field in value}


@dataclass(frozen=True)
class Retrieval:
    selection: Selection
    messages: dict[str, dict[str, Any]]
    failures: dict[str, str]


class ProcessedMessages(Protocol):
    """An app's processed-message store: the expense `Store` or the job tracker's `JobStore`."""

    def filter_new_ids(self, provider: str, message_ids: list[str]) -> list[str]: ...

    def latest_received_at(self, provider: str) -> datetime | None: ...


def _oldest_new_ids(
    gmail: GmailApiClient,
    store: ProcessedMessages,
    new_ids: list[str],
    *,
    received_since: datetime,
    search_terms: str,
    limit: int,
    now: datetime,
) -> list[str]:
    """Return the new IDs in the oldest search window from the start that holds `limit` of them.

    The window ends a day after the newest processed message, or after the start, and doubles
    that distance until it holds the batch or reaches now. Epoch seconds make its bounds exact,
    so every new ID outside it is newer than every one inside it.
    """
    pending = set(new_ids)
    latest = store.latest_received_at("gmail")
    base = max(received_since, latest) if latest is not None else received_since
    span = timedelta(days=1)
    while base + span < now:
        terms = (
            "in:inbox",
            search_terms,
            f"after:{int(received_since.timestamp()) - 1}",
            f"before:{int((base + span).timestamp())}",
        )
        window = [
            message_id
            for message_id in gmail.list_message_ids(" ".join(term for term in terms if term))
            if message_id in pending
        ]
        if len(window) >= limit:
            return window
        span *= 2
    return new_ids


def retrieve_new_messages(
    gmail: GmailApiClient,
    store: ProcessedMessages,
    *,
    received_since: datetime,
    limit: int,
    full: bool = False,
    skip_filter: Callable[[dict[str, Any]], bool] | None = is_filtered,
    search_terms: str = SKIPPED_SEARCH_TERMS,
    new_metadata_only: bool = False,
) -> Retrieval:
    """Search, select, and fetch new inbox messages as the connector sync does.

    The defaults are the expense sync's. The job tracker passes no skip filter, its job words
    as the search terms, and turns on `new_metadata_only`: the search starts at the exact start, IDs
    already processed get no metadata request and are reported as already processed in search
    order, and while more new IDs remain than the limit, only those in the oldest window that
    holds the limit get one. The others are deferred, newest last, as search order suggests.

    A failed search or metadata request raises before anything is fetched. A failed fetch is
    reported for its message, which stays unprocessed and is retried by a later sync.
    """
    latest = None if full else store.latest_received_at("gmail")
    message_ids = gmail.list_message_ids(
        search_query(received_since, latest, search_terms=search_terms, exact=new_metadata_only)
    )
    known: list[str] = []
    later: list[str] = []
    if new_metadata_only:
        new_ids = store.filter_new_ids("gmail", message_ids)
        new_set = set(new_ids)
        known = [message_id for message_id in message_ids if message_id not in new_set]
        message_ids = new_ids
        if len(new_ids) > limit:
            message_ids = _oldest_new_ids(
                gmail,
                store,
                new_ids,
                received_since=received_since,
                search_terms=search_terms,
                limit=limit,
                now=datetime.now(timezone.utc),
            )
            window = set(message_ids)
            later = [message_id for message_id in reversed(new_ids) if message_id not in window]
    metadata = {message_id: gmail.message_metadata(message_id) for message_id in message_ids}
    selection = select_new_messages(
        metadata,
        [],
        lambda new_ids: store.filter_new_ids("gmail", new_ids),
        received_since=received_since,
        limit=limit,
        skip_filter=skip_filter,
    )
    if known or later:
        selection = replace(
            selection,
            already_processed_ids=known + selection.already_processed_ids,
            deferred_ids=selection.deferred_ids + later,
        )
    messages: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    for message_id in selection.message_ids:
        try:
            messages[message_id] = gmail.raw_message(message_id)
        except GmailRetrievalError as error:
            failures[message_id] = f"Gmail retrieval error: {error}"
    return Retrieval(selection, messages, failures)
