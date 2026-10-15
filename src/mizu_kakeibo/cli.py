from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .constants import SYNC_BATCH_LIMIT, SYNC_RECEIVED_SINCE
from .evaluation import evaluate, load_dataset
from .models import AnalysisDecision, Transaction
from .normalization import NormalizationError, normalize_gmail_message, parse_inbox_message
from .ollama import AnalysisError, OllamaAnalyser
from .processor import InboxProcessor, ProcessingStatus
from .selection import select_new_messages
from .storage import ReviewResolutionError, Store
from .transcript import collect_raw_messages, connector_message_metadata, current_session_transcript


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def _objects(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _write_json(value: Any, path: Path | None) -> None:
    rendered = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if path is None:
        sys.stdout.write(rendered)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")


def _store(args: argparse.Namespace) -> Store:
    return Store(args.database, args.ledger)


def command_normalize(args: argparse.Namespace) -> int:
    source = _read_json(args.input)
    messages = [normalize_gmail_message(value) for value in _objects(source)]
    output: Any = [message.model_dump(mode="json") for message in messages]
    if not isinstance(source, list):
        output = output[0]
    _write_json(output, args.output)
    return 0


def _id_list(value: Any, key: str) -> list[str]:
    message_ids = value.get(key, []) if isinstance(value, dict) else value
    if not isinstance(message_ids, list) or not all(
        isinstance(message_id, str) and message_id for message_id in message_ids
    ):
        raise ValueError(f"input must be an ID array or an object with {key}")
    return message_ids


def command_process(args: argparse.Namespace) -> int:
    messages = [parse_inbox_message(value) for value in _objects(_read_json(args.input))]
    selection = _read_json(args.selection) if args.selection is not None else {}
    with OllamaAnalyser() as analyser:
        results = InboxProcessor(_store(args), analyser).process_batch(
            messages, reevaluate=args.reevaluate
        )
    summary: dict[str, list[str]] = {
        "expense": [],
        "not_expense": [],
        "needs_review": [],
        "skipped": _id_list(selection, "already_processed_ids"),
        "failed": [],
        "deferred": _id_list(selection, "deferred_ids"),
    }
    for result in results:
        if result.status is ProcessingStatus.PROCESSED and result.decision is not None:
            summary[result.decision].append(result.message_id)
        else:
            summary[result.status.value].append(result.message_id)
    _write_json(
        {
            "summary": summary,
            "results": [
                {
                    "provider": result.provider,
                    "message_id": result.message_id,
                    "status": result.status.value,
                    "decision": result.decision,
                    "error": result.error,
                }
                for result in results
            ],
        },
        None,
    )
    return 1 if any(result.status is ProcessingStatus.FAILED for result in results) else 0


def _session(args: argparse.Namespace) -> Path:
    return args.session if args.session is not None else current_session_transcript(Path.cwd())


def command_select_new(args: argparse.Namespace) -> int:
    metadata, incomplete_thread_ids = connector_message_metadata(_session(args))
    store = _store(args)
    selection = select_new_messages(
        metadata,
        incomplete_thread_ids,
        lambda message_ids: store.filter_new_ids("gmail", message_ids),
        received_since=datetime.fromisoformat(args.received_since),
        limit=args.limit,
    )
    _write_json(selection.as_json(), args.output)
    if args.output is not None:
        _write_json(
            {key: len(message_ids) for key, message_ids in selection.as_json().items()}, None
        )
    return 0


def command_collect_raw(args: argparse.Namespace) -> int:
    message_ids = _id_list(_read_json(args.input), "message_ids")
    messages, missing = collect_raw_messages(_session(args), message_ids)
    _write_json(messages, args.output)
    _write_json({"collected": [message["id"] for message in messages], "missing": missing}, None)
    return 1 if missing else 0


def command_evaluate(args: argparse.Namespace) -> int:
    dataset = load_dataset(_read_json(args.dataset))
    with OllamaAnalyser() as analyser:
        report = evaluate(dataset, analyser, runs=args.runs)
    if args.output is not None:
        _write_json(report, args.output)
    _write_json(report["summary"], None)
    return 0


def command_filter_new(args: argparse.Namespace) -> int:
    source = _read_json(args.input)
    message_ids = source.get("message_ids") if isinstance(source, dict) else source
    if not isinstance(message_ids, list) or not all(
        isinstance(message_id, str) and message_id for message_id in message_ids
    ):
        raise ValueError("input must be an ID array or a Gmail search response with message_ids")
    unique_ids = list(dict.fromkeys(message_ids))
    new_ids = _store(args).filter_new_ids("gmail", unique_ids)
    new_id_set = set(new_ids)
    _write_json(
        {
            "message_ids": new_ids,
            "already_processed_ids": [
                message_id for message_id in unique_ids if message_id not in new_id_set
            ],
        },
        None,
    )
    return 0


def _gmail_url(message_id: str, thread_id: str | None) -> str | None:
    """Build the Gmail web link the connector reports, from hexadecimal Gmail IDs."""
    try:
        message = int(message_id, 16)
        thread = int(thread_id, 16) if thread_id else None
    except ValueError:
        return None
    if thread is None:
        return None
    return f"https://mail.google.com/mail/#all/thread-f:{thread}|msg-f:{message}"


def command_review_list(args: argparse.Namespace) -> int:
    items = _store(args).list_review_items()
    _write_json(
        [
            {
                "provider": item.provider,
                "message_id": item.message_id,
                "gmail_url": _gmail_url(item.message_id, item.thread_id)
                if item.provider == "gmail"
                else None,
                "reason": item.reason,
                "missing_fields": item.missing_fields,
                "created_at": item.created_at,
            }
            for item in items
        ],
        None,
    )
    return 0


def command_review_resolve(args: argparse.Namespace) -> int:
    decision = AnalysisDecision(args.decision)
    transaction = None
    supplied_transaction_value = any(
        value is not None
        for value in (args.merchant, args.amount_minor, args.currency, args.transaction_date)
    )
    if decision is AnalysisDecision.EXPENSE or supplied_transaction_value:
        transaction = Transaction(
            merchant=args.merchant,
            amount_minor=args.amount_minor,
            currency=args.currency,
            transaction_date=date.fromisoformat(args.transaction_date)
            if args.transaction_date
            else None,
        )
    _store(args).resolve_review(args.provider, args.message_id, decision, transaction)
    _write_json(
        {
            "provider": args.provider,
            "message_id": args.message_id,
            "decision": decision.value,
            "status": "resolved",
        },
        None,
    )
    return 0


def _add_storage_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", type=Path, default=Path("mizu-kakeibo.sqlite3"))
    parser.add_argument("--ledger", type=Path, default=Path("mizu-kakeibo-ledger.csv"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mizu-kakeibo")
    subparsers = parser.add_subparsers(dest="command", required=True)

    normalize = subparsers.add_parser("normalize", help="normalize Gmail MCP full messages")
    normalize.add_argument("input", type=Path)
    normalize.add_argument("--output", type=Path)
    normalize.set_defaults(handler=command_normalize)

    process = subparsers.add_parser(
        "process", help="normalize and analyse full or normalized inbox messages"
    )
    process.add_argument("input", type=Path)
    process.add_argument("--reevaluate", action="store_true")
    process.add_argument(
        "--selection",
        type=Path,
        help="select-new or filter-new output, to report its skipped and deferred IDs",
    )
    _add_storage_options(process)
    process.set_defaults(handler=command_process)

    evaluate_parser = subparsers.add_parser(
        "evaluate", help="score the analyser against a private labelled dataset"
    )
    evaluate_parser.add_argument("dataset", type=Path)
    evaluate_parser.add_argument("--runs", type=int, default=3)
    evaluate_parser.add_argument("--output", type=Path)
    evaluate_parser.set_defaults(handler=command_evaluate)

    filter_new = subparsers.add_parser(
        "filter-new", help="filter Gmail search IDs before full-message retrieval"
    )
    filter_new.add_argument("input", type=Path)
    _add_storage_options(filter_new)
    filter_new.set_defaults(handler=command_filter_new)

    select_new = subparsers.add_parser(
        "select-new",
        help="select new inbox message IDs from this session's Gmail connector searches",
    )
    select_new.add_argument("--session", type=Path)
    select_new.add_argument("--received-since", default=SYNC_RECEIVED_SINCE)
    select_new.add_argument("--limit", type=int, default=SYNC_BATCH_LIMIT)
    select_new.add_argument("--output", type=Path)
    _add_storage_options(select_new)
    select_new.set_defaults(handler=command_select_new)

    collect_raw = subparsers.add_parser(
        "collect-raw",
        help="collect this session's Gmail connector RAW messages for the selected IDs",
    )
    collect_raw.add_argument("input", type=Path)
    collect_raw.add_argument("--session", type=Path)
    collect_raw.add_argument("--output", type=Path, required=True)
    collect_raw.set_defaults(handler=command_collect_raw)

    review_list = subparsers.add_parser("review-list", help="list unresolved review items")
    _add_storage_options(review_list)
    review_list.set_defaults(handler=command_review_list)

    resolve = subparsers.add_parser("review-resolve", help="resolve one review item")
    resolve.add_argument("provider", choices=["gmail"])
    resolve.add_argument("message_id")
    resolve.add_argument("decision", choices=["expense", "not_expense"])
    resolve.add_argument("--merchant")
    resolve.add_argument("--amount-minor", type=int)
    resolve.add_argument("--currency")
    resolve.add_argument("--transaction-date")
    _add_storage_options(resolve)
    resolve.set_defaults(handler=command_review_resolve)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (
        AnalysisError,
        FileNotFoundError,
        json.JSONDecodeError,
        NormalizationError,
        ReviewResolutionError,
        ValidationError,
        ValueError,
    ) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
