from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

CLAUDE_PROJECTS_DIRECTORY = Path.home() / ".claude" / "projects"


def current_session_transcript(
    working_directory: Path, projects_directory: Path = CLAUDE_PROJECTS_DIRECTORY
) -> Path:
    """Return the most recently written Claude Code transcript for a working directory."""
    project = projects_directory / re.sub(r"[^A-Za-z0-9-]", "-", str(working_directory))
    transcripts = sorted(project.glob("*.jsonl"), key=lambda path: path.stat().st_mtime)
    if not transcripts:
        raise FileNotFoundError(f"no Claude Code session transcript under {project}")
    return transcripts[-1]


def _is_gmail_tool(name: str, tools: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return "gmail" in lowered and any(lowered.endswith(tool) for tool in tools)


def _tool_result_texts(transcript: Path, tools: tuple[str, ...]) -> Iterator[str]:
    """Yield result texts of the named Gmail connector tools recorded in a session transcript."""
    tool_names: dict[str, str] = {}
    with transcript.open(encoding="utf-8") as source:
        for line in source:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            content = (record.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                    tool_names[block["id"]] = str(block.get("name", ""))
                elif block.get("type") == "tool_result" and _is_gmail_tool(
                    tool_names.get(block.get("tool_use_id"), ""), tools
                ):
                    yield from _texts(block.get("content"))
    # Results too large to show inline are saved beside the transcript, named after the tool.
    saved_results = transcript.with_suffix("") / "tool-results"
    for path in sorted(saved_results.glob("*.txt")):
        if _is_gmail_tool(path.stem.rsplit("-", 1)[0].replace("-", "_"), tools):
            yield path.read_text(encoding="utf-8")


def _texts(content: Any) -> Iterator[str]:
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                yield part["text"]


def _raw_messages(text: str) -> Iterator[dict[str, Any]]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return
    if isinstance(value, list):
        for part in value:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                yield from _raw_messages(part["text"])
    elif (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("raw"), str)
        and "payload" not in value
    ):
        yield value


def _threads(value: Any) -> Iterator[dict[str, Any]]:
    if not isinstance(value, dict):
        return
    if isinstance(value.get("threads"), list):
        for thread in value["threads"]:
            yield from _threads(thread)
    elif isinstance(value.get("id"), str) and isinstance(value.get("messages"), list):
        yield value


def connector_message_metadata(
    transcript: Path,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Gather message metadata from connector `search_threads` and `get_thread` results.

    Returns metadata keyed by message ID, and the IDs of threads whose results listed fewer
    messages than the thread's message count, which need `get_thread` before selection.
    """
    messages: dict[str, dict[str, Any]] = {}
    message_counts: dict[str, int] = {}
    listed: dict[str, set[str]] = {}
    for text in _tool_result_texts(transcript, ("search_threads", "get_thread")):
        if '"messages"' not in text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        for thread in _threads(value):
            thread_id = thread["id"]
            count = thread.get("messageCount")
            if isinstance(count, int) and not isinstance(count, bool):
                message_counts[thread_id] = max(count, message_counts.get(thread_id, 0))
            for message in thread["messages"]:
                if isinstance(message, dict) and isinstance(message.get("id"), str):
                    messages[message["id"]] = message
                    listed.setdefault(thread_id, set()).add(message["id"])
    incomplete = [
        thread_id
        for thread_id, count in message_counts.items()
        if len(listed.get(thread_id, ())) < count
    ]
    return messages, incomplete


def collect_raw_messages(
    transcript: Path, message_ids: Iterable[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Find connector `get_message(RAW)` results for the requested IDs in a session transcript.

    Returns the found responses in request order and the IDs that were not found.
    """
    wanted = list(dict.fromkeys(message_ids))
    wanted_set = set(wanted)
    found: dict[str, dict[str, Any]] = {}
    for text in _tool_result_texts(transcript, ("get_message",)):
        if '"raw"' not in text:
            continue
        for message in _raw_messages(text):
            if message["id"] in wanted_set:
                found[message["id"]] = message
    return (
        [found[message_id] for message_id in wanted if message_id in found],
        [message_id for message_id in wanted if message_id not in found],
    )
