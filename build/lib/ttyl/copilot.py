"""GitHub Copilot CLI sessions (~/.copilot/session-state/<id>/events.jsonl) -> Session.

Each line is {type, id, parentId, timestamp, data} (event types from the
copilot-sdk's generated session-events): session.start / session.resume
(data.context.cwd), user.message (data.content), assistant.turn_start /
assistant.turn_end, assistant.message (data.content, data.toolRequests),
tool.execution_start (data.toolName, data.arguments), tool.execution_complete,
session.task_complete, session.shutdown. A sibling workspace.yaml has cwd,
branch and name. Copilot CLI is closed source: this follows its SDK's types.
"""

from __future__ import annotations

import re
from pathlib import Path

from .model import Session, Turn, command_dir, one_line, parse_ts, short_command

EDIT_TOOLS = {"edit", "create", "write", "str_replace", "str_replace_editor", "apply_patch"}
SHELL_TOOLS = {"bash", "shell", "powershell"}
_GIT_COMMIT = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+commit\b")


def read_workspace(path: Path) -> dict[str, str]:
    """workspace.yaml is flat `key: value` lines; no YAML library needed."""
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            if ":" in line and not line.startswith((" ", "-", "#")):
                key, value = line.split(":", 1)
                out[key.strip()] = value.strip().strip("'\"")
    except OSError:
        pass
    return out


class CopilotParser:
    def __init__(self, session: Session):
        self.s = session
        self.turn: Turn | None = None
        self.calls: dict[str, tuple[str, dict, Turn]] = {}
        ws = read_workspace(session.path.parent / "workspace.yaml") if session.path else {}
        session.id = ws.get("id") or (session.path.parent.name if session.path else session.id)
        session.cwd = ws.get("cwd") or session.cwd
        session.branch = ws.get("branch") or session.branch
        session.ai_title = ws.get("name") or ""
        session.entrypoint = "cli"

    def feed(self, d: dict) -> None:
        s = self.s
        kind, data = d.get("type", ""), d.get("data") or {}
        ts = parse_ts(d.get("timestamp"))
        if ts:
            s.started = s.started or ts
            s.updated = ts
        if kind in ("session.start", "session.resume"):
            s.cwd = ((data.get("context") or {}).get("cwd")) or s.cwd
        elif kind == "user.message":
            text = str(data.get("content") or "").strip()
            if text:
                if self.turn:
                    self.turn.done = True
                self.turn = Turn(started=ts or s.updated, prompt=text)
                s.turns.append(self.turn)
        elif kind == "assistant.turn_start":
            if self.turn:
                self.turn.done = False
        elif kind == "assistant.message":
            t = self._current(ts)
            t.had_agent = True
            t.ended = ts or t.ended
            if str(data.get("content") or "").strip():
                t.reply = str(data["content"]).strip()
        elif kind == "tool.execution_start":
            t = self._current(ts)
            name, args = data.get("toolName", "?"), data.get("arguments") or {}
            t.tools[name] += 1
            if name in EDIT_TOOLS:
                path = args.get("path") or args.get("file_path") or args.get("filePath") or ""
                t.add_file(path)
                t.last_action = "✎ " + s.rel(path)
            elif name in SHELL_TOOLS:
                cmd = args.get("command", "")
                t.commands.append(cmd)
                t.last_action = "$ " + one_line(short_command(cmd), 70)
            else:
                t.last_action = name
            if data.get("toolCallId"):
                self.calls[data["toolCallId"]] = (name, args, t)
        elif kind == "tool.execution_complete":
            name, args, t = self.calls.pop(data.get("toolCallId", ""), (None, {}, self.turn))
            if t is None:
                return
            if data.get("success") is False or data.get("error"):
                t.tool_errors += 1
            elif name in SHELL_TOOLS and _GIT_COMMIT.search(args.get("command", "")):
                t.committed = True
                t.commit_dirs.append(command_dir(args.get("command", ""), s.cwd))
        elif kind in ("assistant.turn_end", "session.task_complete", "session.shutdown"):
            if self.turn:
                self.turn.done = True
                self.turn.ended = ts or self.turn.ended

    def _current(self, ts) -> Turn:
        if self.turn is None:
            self.turn = Turn(started=ts or self.s.updated, prompt="(resumed)", origin="system")
            self.s.turns.append(self.turn)
        return self.turn
