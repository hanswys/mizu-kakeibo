from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mizu_kakeibo.transcript import (
    collect_raw_messages,
    connector_message_metadata,
    current_session_transcript,
)

GMAIL = "mcp__claude_ai_Gmail__"


def raw_response(message_id: str) -> dict:
    return {"id": message_id, "threadId": message_id, "internalDate": "1", "raw": "UmF3"}


def write_transcript(path: Path, calls: list[tuple[str, object]]) -> Path:
    lines = []
    for index, (tool, result) in enumerate(calls):
        use_id = f"toolu_{index}"
        lines.append({"message": {"content": [{"type": "tool_use", "id": use_id, "name": tool}]}})
        content = result if isinstance(result, str) else json.dumps(result)
        lines.append(
            {
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": use_id,
                            "content": [{"type": "text", "text": content}],
                        }
                    ]
                }
            }
        )
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\nnot json\n")
    return path


def test_collects_inline_and_saved_raw_results_in_request_order(tmp_path) -> None:
    transcript = write_transcript(
        tmp_path / "session.jsonl",
        [
            (f"{GMAIL}get_message", raw_response("a1")),
            (f"{GMAIL}get_message", "Error: result exceeds maximum allowed tokens."),
        ],
    )
    saved = tmp_path / "session" / "tool-results"
    saved.mkdir(parents=True)
    (saved / "mcp-claude_ai_Gmail-get_message-123.txt").write_text(json.dumps(raw_response("b2")))

    messages, missing = collect_raw_messages(transcript, ["b2", "a1", "c3", "a1"])

    assert [message["id"] for message in messages] == ["b2", "a1"]
    assert missing == ["c3"]


def test_ignores_raw_shaped_output_from_other_tools(tmp_path) -> None:
    transcript = write_transcript(tmp_path / "session.jsonl", [("Bash", raw_response("a1"))])
    saved = tmp_path / "session" / "tool-results"
    saved.mkdir(parents=True)
    (saved / "bash-123.txt").write_text(json.dumps(raw_response("a1")))

    assert collect_raw_messages(transcript, ["a1"]) == ([], ["a1"])


def test_gathers_thread_metadata_and_reports_incomplete_threads(tmp_path) -> None:
    transcript = write_transcript(
        tmp_path / "session.jsonl",
        [
            (
                f"{GMAIL}search_threads",
                {
                    "threads": [
                        {"id": "t1", "messageCount": 2, "messages": [{"id": "m1"}]},
                        {"id": "t2", "messageCount": 3, "messages": [{"id": "m3"}]},
                    ]
                },
            ),
            (
                f"{GMAIL}get_thread",
                {"id": "t1", "messageCount": 2, "messages": [{"id": "m1"}, {"id": "m2"}]},
            ),
            ("Bash", {"id": "t9", "messageCount": 1, "messages": [{"id": "m9"}]}),
        ],
    )

    metadata, incomplete = connector_message_metadata(transcript)

    assert sorted(metadata) == ["m1", "m2", "m3"]
    assert incomplete == ["t2"]


def test_finds_the_newest_transcript_for_a_working_directory(tmp_path) -> None:
    project = tmp_path / "-work-my-app-v1"
    project.mkdir()
    older = project / "older.jsonl"
    newer = project / "newer.jsonl"
    older.write_text("")
    newer.write_text("")
    os.utime(older, (1, 1))

    assert current_session_transcript(Path("/work/my_app.v1"), tmp_path) == newer
    with pytest.raises(FileNotFoundError):
        current_session_transcript(Path("/elsewhere"), tmp_path)
