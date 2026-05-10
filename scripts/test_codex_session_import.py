#!/usr/bin/env python3
"""Regression tests for Codex JSONL import compatibility."""

import json
import tempfile
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import serve


def write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False))
            f.write("\n")


def read_roles_and_messages(db, session_id: str):
    return db.execute(
        "SELECT role, text FROM messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()


def run_index(tmp_root: Path, users: dict[str, str]) -> tuple:
    # These tests mutate serve module globals; keep this file sequential.
    old_db_path = serve.DB_PATH
    old_users = dict(serve.USERS)

    serve.DB_PATH = tmp_root / "archive.db"
    serve.USERS = users
    db = serve.init_db()
    try:
        serve.index_sessions(db)
        return db
    finally:
        # caller closes db; only restore process globals here
        serve.DB_PATH = old_db_path
        serve.USERS = old_users


def test_codex_event_msg_parsing():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        codex_user_dir = root / "codex_sessions"
        sid = "codex-event-session"

        write_jsonl(codex_user_dir / f"{sid}.jsonl", [
            {
                "type": "event_msg",
                "title": "Codex Event Session",
                "cwd": "/tmp/codex-event",
                "model": "codex-demo",
                "message": {
                    "type": "user_message",
                    "createdAt": "2026-05-10T10:00:00Z",
                    "content": [{"type": "text", "text": "Guten Tag, bitte kurze Zusammenfassung."}],
                },
            },
            {
                "type": "event_msg",
                "message": {
                    "type": "agent_message",
                    "createdAt": "2026-05-10T10:00:01Z",
                    "content": "Alles klar, ich fasse zusammen.",
                },
            },
            {
                "type": "event_msg",
                "message": {
                    "type": "user_message",
                    "createdAt": "2026-05-10T10:00:02Z",
                    "content": "Noch eine Frage.",
                },
            },
            {
                "type": "response_item",
                "input_text": "antworten",
                "output_text": "unterdrückt",
            },
        ])

        db = run_index(root, {"codex": str(codex_user_dir)})
        try:
            rows = read_roles_and_messages(db, sid)
            assert [r[0] for r in rows] == ["user", "assistant", "user"], rows
            title, cwd, model = db.execute(
                "SELECT title, cwd, model FROM sessions WHERE id = ?",
                (sid,),
            ).fetchone()
            assert title == "Codex Event Session", title
            assert cwd == "/tmp/codex-event", cwd
            assert model == "codex-demo", model
            assert "Guten Tag" in rows[0][1], rows[0]
        finally:
            db.close()


def test_codex_payload_event_msg_parsing():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        codex_user_dir = root / "codex_sessions"
        sid = "rollout-2026-05-10T12-00-00-019e-codex-realistic"

        write_jsonl(codex_user_dir / f"{sid}.jsonl", [
            {
                "timestamp": "2026-05-10T12:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "019e-codex-realistic",
                    "cwd": "/tmp/codex-realistic",
                },
            },
            {
                "timestamp": "2026-05-10T12:00:01Z",
                "type": "turn_context",
                "payload": {
                    "model": "gpt-5.3-codex-spark",
                    "cwd": "/tmp/codex-realistic",
                },
            },
            {
                "timestamp": "2026-05-10T12:00:02Z",
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "Bitte importiere diese Codex-Session.",
                    "text_elements": [],
                },
            },
            {
                "timestamp": "2026-05-10T12:00:03Z",
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "message": "Ich importiere sie.",
                    "phase": "commentary",
                },
            },
            {
                "timestamp": "2026-05-10T12:00:04Z",
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "Danke.",
                    "text_elements": [],
                },
            },
            {
                "timestamp": "2026-05-10T12:00:05Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "Nicht archivieren."}],
                },
            },
        ])

        db = run_index(root, {"codex": str(codex_user_dir)})
        try:
            rows = read_roles_and_messages(db, sid)
            assert [r[0] for r in rows] == ["user", "assistant", "user"], rows
            assert "Bitte importiere" in rows[0][1], rows
            assert all("Nicht archivieren" not in r[1] for r in rows), rows
            title, cwd, model = db.execute(
                "SELECT title, cwd, model FROM sessions WHERE id = ?",
                (sid,),
            ).fetchone()
            assert title == "Bitte importiere diese Codex-Session.", title
            assert cwd == "/tmp/codex-realistic", cwd
            assert model == "gpt-5.3-codex-spark", model
        finally:
            db.close()


def test_codex_response_item_fallback():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        codex_user_dir = root / "codex_sessions"
        sid = "codex-response-item"
        write_jsonl(codex_user_dir / f"{sid}.jsonl", [
            {
                "type": "response_item",
                "title": "Codex ResponseItem",
                "cwd": "/tmp/codex-response",
                "model": "codex-response",
                "input_text": "Was ist die Hauptstadt von Österreich?",
                "output_text": "Wien.",
                "createdAt": "2026-05-10T11:00:00Z",
            },
            {
                "type": "response_item",
                "input_text": "Und von Deutschland?",
                "output_text": "Berlin.",
                "createdAt": "2026-05-10T11:00:01Z",
            },
            {
                "type": "response_item",
                "input_text": "Kurze Rückfrage.",
                "output_text": "Natürlich.",
                "createdAt": "2026-05-10T11:00:02Z",
            },
        ])

        db = run_index(root, {"codex": str(codex_user_dir)})
        try:
            rows = read_roles_and_messages(db, sid)
            assert [r[0] for r in rows] == [
                "user",
                "assistant",
                "user",
                "assistant",
                "user",
                "assistant",
            ], rows
            title, cwd, model = db.execute(
                "SELECT title, cwd, model FROM sessions WHERE id = ?",
                (sid,),
            ).fetchone()
            assert title == "Codex ResponseItem", title
            assert cwd == "/tmp/codex-response", cwd
            assert model == "codex-response", model
            assert "Wien." in rows[1][1], rows[1]
        finally:
            db.close()


def test_codex_payload_response_item_fallback():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        codex_user_dir = root / "codex_sessions"
        sid = "codex-payload-response-item"
        write_jsonl(codex_user_dir / f"{sid}.jsonl", [
            {
                "timestamp": "2026-05-10T13:00:00Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.3-codex"},
            },
            {
                "timestamp": "2026-05-10T13:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Erste Frage?"}],
                },
            },
            {
                "timestamp": "2026-05-10T13:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Erste Antwort."}],
                },
            },
            {
                "timestamp": "2026-05-10T13:00:03Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Zweite Frage?"}],
                },
            },
            {
                "timestamp": "2026-05-10T13:00:04Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "system",
                    "input_text": "Nicht als User importieren.",
                    "output_text": "Nicht als Assistant importieren.",
                },
            },
        ])

        db = run_index(root, {"codex": str(codex_user_dir)})
        try:
            rows = read_roles_and_messages(db, sid)
            assert [r[0] for r in rows] == ["user", "assistant", "user"], rows
            assert rows[0][1] == "Erste Frage?", rows
            assert rows[1][1] == "Erste Antwort.", rows
            assert all("Nicht als" not in r[1] for r in rows), rows
            model = db.execute("SELECT model FROM sessions WHERE id = ?", (sid,)).fetchone()[0]
            assert model == "gpt-5.3-codex", model
        finally:
            db.close()


def test_claude_compat_remains_unchanged():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        claude_dir = root / "claude"
        sid = "claude-legacy"
        write_jsonl(claude_dir / f"{sid}.jsonl", [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "createdAt": "2026-05-10T09:00:00Z",
                    "content": "Wie lautet der Projektname?",
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "createdAt": "2026-05-10T09:00:01Z",
                    "content": "Session Archiv.",
                },
            },
            {
                "type": "summary",
                "message": {
                    "role": "system",
                    "createdAt": "2026-05-10T09:00:02Z",
                    "content": "Kurzes Test-Set.",
                },
            },
        ])

        db = run_index(root, {"anton": str(claude_dir)})
        try:
            rows = read_roles_and_messages(db, sid)
            assert [r[0] for r in rows] == ["user", "assistant", "system"], rows
            title = db.execute("SELECT title FROM sessions WHERE id = ?", (sid,)).fetchone()[0]
            assert title.startswith("Wie lautet"), title
            assert title == "Wie lautet der Projektname?", title
        finally:
            db.close()


def main():
    test_codex_event_msg_parsing()
    test_codex_payload_event_msg_parsing()
    test_codex_response_item_fallback()
    test_codex_payload_response_item_fallback()
    test_claude_compat_remains_unchanged()
    print("test_codex_session_import.py: OK")


if __name__ == "__main__":
    main()
