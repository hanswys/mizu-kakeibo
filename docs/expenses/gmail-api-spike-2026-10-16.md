# Gmail API Spike, 2026-10-16

Evidence for [Gmail API retrieval](inbox-expense-contract.md#gmail-api-retrieval) in Version 1. A throwaway script outside the repository called the Gmail API through a Desktop OAuth client in the user's own Google Cloud project, whose consent screen was External and in Testing. No message was processed, no message content was printed, and the live database was read only through a copy or a read-only connection. The script's tokens and fetched messages were deleted afterwards.

## Scope

- `gmail.readonly` was the only permission shown at consent and the only scope granted.
- A token granted only `gmail.metadata` was refused `messages.list` with a `q` search (403), and `messages.get` in `raw` and `full` formats (403). It could list by label ID and get messages in `metadata` format. It can therefore neither run the sync search nor read a body.
- `gmail.addons.current.message.readonly` only works in a Workspace add-on's per-message context, so a script cannot use it. Google classes both `gmail.readonly` and `gmail.metadata` as restricted, so the narrower one would not have lowered the classification either.

## Search parity

The connector's `search_threads` and the API ran the same `search-query` output within minutes of each other.

- Both returned the same 25 messages in 25 single-message threads, on one page. `messages.list` returned the same 25 IDs as `threads.list` with `threads.get`.
- `threadId`, `internalDate`, `labelIds`, `historyId`, and `sizeEstimate` matched for every message.
- `select_new_messages` gave an identical selection from either source.
- Multi-message threads did not occur, so `messages.list` and thread enumeration were not compared on them.

## Sender

- The connector's metadata `sender` is a bare address.
- The API's `metadata` format returns the `From` header, which carries the display name.
- The skip filter parses either form, and its decisions matched for all 25 messages.

## Raw and envelope parity

Five messages were chosen for coverage: an HTML-only message of about 735 KB with two attachments, a body truncated by the length limit, a six-part message with base64 parts and an attachment, a `windows-1252` HTML message, and an `iso-8859-1` plain-text message.

- For all five, the API's `raw` string was identical to the connector's `raw` string from an earlier sync.
- The existing converter and normalizer produced envelopes identical to those stored in the live database for all five.
- The API's `raw` response carries `snippet`, which holds body text, and no `viewUrl`. The connector's carries `viewUrl` and no `snippet`. The converter ignores both fields. `internalDate` is a string in both.
- One message's labels and `historyId` had changed since the earlier sync because it had been read. This is a mailbox change over time, not a provider difference.

## Token lifetime

- An access token lasts one hour, and the client library refreshes it from the stored refresh token.
- Google documents that an External app in Testing receives refresh tokens that expire after seven days. Internal needs a Workspace organization, which a personal account does not have.
- Moving the app to In production without verification is allowed for personal use under 100 users. Consent then shows an unverified-app warning. Whether the refresh token then outlives seven days was not yet observed when this note was written.
- A refresh token also stops working after six months without use, when the account password changes, when the user removes the app's access, or when more than 100 refresh tokens exist for the account and client.

## Quotas

Google lists 6,000 quota units per user per minute and 1,200,000 per project per minute, at no cost. `messages.list` costs 5 units, `messages.get` 20, `threads.list` 10, and `threads.get` 40. A sync of 25 messages through `messages.list` and two `messages.get` calls per message uses about 1,000 units.
