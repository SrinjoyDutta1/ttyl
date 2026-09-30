"""OpenCode sessions, from its SQLite database (~/.local/share/opencode/opencode.db).

Schema (sst/opencode packages/core/src/session/sql.ts): `session` (id, directory,
title, parent_id, time_created/time_updated/time_archived in ms), `message`
(id, session_id, time_created, data JSON: role, time.created/completed, error)
and `part` (message_id, session_id, data JSON: type text | tool | patch | ...).
Tool parts: {tool: edit|write|apply_patch|bash|..., state: {status, input,
metadata}}; patch parts list the absolute paths a step edited.

A turn is done once its assistant message has `time.completed`. Approval waits
live only in OpenCode's memory (the tool part just stays "running").
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .model import Session, Turn, command_dir, one_line, parse_ts, short_command

EDIT_TOOLS = {"edit", "write", "multiedit"}
SHELL_TOOLS = {"bash"}
PLACEHOLDER_TITLE = re.compile(r"^(New session|Child session) - \d{4}-")
_GIT_COMMIT = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+commit\b")


def _json(raw) -> dict:
    try:
        out = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        return out if isinstance(out, dict) else {}
    except ValueError:
        return {}


def load(db: Path, since_ms: int = 0) -> list[Session]:
    """Top-level sessions updated since `since_ms` (subagent sessions have a parent and are skipped)."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return []
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("select * from session where parent_id is null and time_updated >= ? "
                           "order by time_updated desc limit 200", (since_ms,)).fetchall()
        out = []
        for row in rows:
            r = dict(row)
            messages = con.execute("select id, time_created, data from message where session_id = ? "
                                   "order by time_created, id", (r["id"],)).fetchall()
            parts = con.execute("select message_id, data from part where session_id = ? order by id",
                                (r["id"],)).fetchall()
            out.append(build(r, messages, parts, db))
        return out
    except sqlite3.Error:
        return []
    finally:
        con.close()


def build(r: dict, messages, parts, db: Path | None = None) -> Session:
    s = Session(agent="opencode", id=r["id"], path=db, cwd=r.get("directory") or "", entrypoint="cli",
                started=parse_ts(r.get("time_created")), updated=parse_ts(r.get("time_updated")),
                archived=bool(r.get("time_archived")))
    title = r.get("title") or ""
    if title and not PLACEHOLDER_TITLE.match(title):
        s.ai_title = title
    by_message: dict[str, list[dict]] = {}
    for m_id, data in parts:
        by_message.setdefault(m_id, []).append(_json(data))
    turn: Turn | None = None
    for m_id, created, data in messages:
        d = _json(data)
        ps = by_message.get(m_id, [])
        text = "\n".join(p.get("text", "") for p in ps if p.get("type") == "text" and not p.get("synthetic")).strip()
        when = parse_ts((d.get("time") or {}).get("created") or created)
        if d.get("role") == "user":
            if not text:
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
        completed = parse_ts((d.get("time") or {}).get("completed"))
        turn.ended = completed or when or turn.ended
        if text:
            turn.reply = text
        if d.get("error"):
            turn.api_error = True
        for p in ps:
            if p.get("type") == "patch":
                for f in p.get("files") or []:
                    turn.add_file(f)
            elif p.get("type") == "tool":
                _tool(s, turn, p)
        turn.done = completed is not None and not any(
            p.get("type") == "tool" and (p.get("state") or {}).get("status") in ("pending", "running") for p in ps)
    return s


def _tool(s: Session, turn: Turn, p: dict) -> None:
    name = p.get("tool", "?")
    state = p.get("state") or {}
    inp = state.get("input") or {}
    status = state.get("status", "")
    turn.tools[name] += 1
    if name in EDIT_TOOLS:
        path = inp.get("filePath") or inp.get("file_path") or ""
        turn.add_file(path)
        turn.last_action = "✎ " + s.rel(path)
    elif name == "apply_patch":
        for f in (state.get("metadata") or {}).get("files") or []:
            turn.add_file(f.get("filePath", "") if isinstance(f, dict) else str(f))
        turn.last_action = "✎ patch"
    elif name in SHELL_TOOLS:
        cmd = inp.get("command", "")
        turn.commands.append(cmd)
        turn.last_action = "$ " + one_line(short_command(cmd), 70)
        if status == "completed" and _GIT_COMMIT.search(cmd):
            turn.committed = True
            turn.commit_dirs.append(command_dir(cmd, inp.get("workdir") or s.cwd))
    else:
        turn.last_action = name
    if status == "error":
        turn.tool_errors += 1
