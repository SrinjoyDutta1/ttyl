"""Goose sessions, from its SQLite database (~/.local/share/goose/sessions/sessions.db).

Schema (block/goose crates/goose/src/session/session_manager.rs): `sessions`
(id, name, working_dir, session_type, created_at, updated_at, ...) and
`messages` (session_id, role, content_json: a list of content blocks,
created_timestamp). Blocks are {"type": "text", "text"} or {"type":
"toolRequest", "toolCall": {"status", "value": {"name", "arguments"}}}; tool
names may carry an extension prefix like `developer__shell`. Built from the
source's schema; tool argument names are best effort.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .model import Session, Turn, command_dir, one_line, parse_ts, short_command

_GIT_COMMIT = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+commit\b")


def _blocks(raw) -> list[dict]:
    try:
        out = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [b for b in out if isinstance(b, dict)] if isinstance(out, list) else []


def load(db: Path, since: str = "") -> list[Session]:
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return []
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("select * from sessions where updated_at >= ? order by updated_at desc limit 200",
                           (since,)).fetchall()
        out = []
        for row in rows:
            r = dict(row)
            if r.get("session_type") not in (None, "", "user") or r.get("parent_session_id"):
                continue  # subagent / scheduled sessions
            messages = con.execute("select role, content_json, created_timestamp from messages "
                                   "where session_id = ? order by id", (r["id"],)).fetchall()
            out.append(build(r, messages, db))
        return out
    except sqlite3.Error:
        return []
    finally:
        con.close()


def build(r: dict, messages, db: Path | None = None) -> Session:
    s = Session(agent="goose", id=str(r["id"]), path=db, cwd=r.get("working_dir") or "", entrypoint="cli",
                ai_title=r.get("name") or "", started=parse_ts(_iso(r.get("created_at"))),
                updated=parse_ts(_iso(r.get("updated_at"))))
    turn: Turn | None = None
    for role, content, created in messages:
        when = parse_ts(created)
        blocks = _blocks(content)
        text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        requests = [b for b in blocks if b.get("type") == "toolRequest"]
        if role == "user":
            if not text:  # tool responses come back as user messages
                if turn:
                    turn.done = False
                continue
            if turn:
                turn.done = True
            turn = Turn(started=when or s.updated, prompt=text)
            s.turns.append(turn)
            continue
        if turn is None:
            turn = Turn(started=when or s.updated, prompt="(resumed)", origin="system")
            s.turns.append(turn)
        turn.had_agent = True
        turn.ended = when or turn.ended
        if text:
            turn.reply = text
        for req in requests:
            call = (req.get("toolCall") or {}).get("value") or {}
            _tool(s, turn, call.get("name", "?"), call.get("arguments") or {})
        turn.done = bool(text) and not requests
    return s


def _tool(s: Session, turn: Turn, name: str, args: dict) -> None:
    short = name.split("__")[-1]  # developer__shell -> shell
    turn.tools[short] += 1
    path = args.get("path") or args.get("file_path") or ""
    if path and (short in ("text_editor", "edit", "write", "str_replace") and args.get("command", "write") != "view"):
        turn.add_file(path)
        turn.last_action = "✎ " + s.rel(path)
    elif short in ("shell", "bash"):
        cmd = args.get("command", "")
        turn.commands.append(cmd)
        turn.last_action = "$ " + one_line(short_command(cmd), 70)
        if _GIT_COMMIT.search(cmd):
            turn.committed = True
            turn.commit_dirs.append(command_dir(cmd, s.cwd))
    else:
        turn.last_action = short


def _iso(value) -> str:
    """Goose timestamps are SQLite's 'YYYY-MM-DD HH:MM:SS' in UTC."""
    if isinstance(value, str) and len(value) >= 19 and value[10] == " ":
        return value[:10] + "T" + value[11:] + "+00:00"
    return value
