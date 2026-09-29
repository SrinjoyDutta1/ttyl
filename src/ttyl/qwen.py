"""Qwen Code sessions (~/.qwen/projects/<cwd-slug>/chats/<sessionId>.jsonl) -> Session.

Format (qwen-code, packages/core/src/services/chatRecordingService.ts): one record
per line {uuid, parentUuid, sessionId, timestamp, type: user|assistant|tool_result|
system, subtype?, cwd, gitBranch?, message?: genai Content, toolCallResult?,
systemPayload?}. Assistant messages carry `functionCall` parts; tool results come
back as `tool_result` records. `system` records with subtype `custom_title` name
the session, and `turn_result` closes a turn.
"""

from __future__ import annotations

import os
import re

from .gemini import IGNORED_PREFIXES, parts_text
from .model import Session, Turn, command_dir, one_line, parse_ts, short_command

EDIT_TOOLS = {"edit", "replace", "write_file"}
SHELL_TOOLS = {"run_shell_command", "exec"}
_GIT_COMMIT = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+commit\b")


class QwenParser:
    def __init__(self, session: Session):
        self.s = session
        self.turn: Turn | None = None
        self.pending: dict[str, tuple[str, dict, Turn]] = {}  # call id -> (name, args, turn)

    def feed(self, d: dict) -> None:
        s = self.s
        ts = parse_ts(d.get("timestamp"))
        if ts:
            s.started = s.started or ts
            s.updated = ts
        s.id = d.get("sessionId") or s.id
        s.cwd = d.get("cwd") or s.cwd
        s.branch = d.get("gitBranch") or s.branch
        kind, sub = d.get("type"), d.get("subtype")
        parts = ((d.get("message") or {}).get("parts")) or []

        if kind == "system":
            payload = d.get("systemPayload") or {}
            if sub == "custom_title" and payload.get("customTitle"):
                s.custom_title = payload["customTitle"]
            elif sub in ("turn_result", "goal_turn_end") and self.turn:
                self.turn.done = True
                self.turn.ended = ts or self.turn.ended
        elif kind == "user":
            if sub == "slash_command":
                return
            text = parts_text(parts).strip()
            if not text or text.startswith(IGNORED_PREFIXES):
                return
            if self.turn:
                self.turn.done = True
            self.turn = Turn(started=ts or s.updated, prompt=text)
            s.turns.append(self.turn)
        elif kind == "assistant":
            t = self.turn or self._resumed(ts)
            t.had_agent = True
            t.ended = ts or t.ended
            text = parts_text(parts).strip()
            calls = [p["functionCall"] for p in parts if isinstance(p, dict) and p.get("functionCall")]
            if text:
                t.reply = text
            for call in calls:
                name, args = call.get("name", "?"), call.get("args") or {}
                t.tools[name] += 1
                if name in EDIT_TOOLS:
                    path = args.get("file_path") or args.get("absolute_path") or ""
                    if path and not os.path.isabs(path) and s.cwd:
                        path = os.path.normpath(os.path.join(s.cwd, path))
                    t.add_file(path)
                    t.last_action = "✎ " + s.rel(path)
                elif name in SHELL_TOOLS:
                    cmd = args.get("command", "")
                    t.commands.append(cmd)
                    t.last_action = "$ " + one_line(short_command(cmd), 70)
                else:
                    t.last_action = name
                if call.get("id"):
                    self.pending[call["id"]] = (name, args, t)
            t.done = bool(text) and not calls
        elif kind == "tool_result":
            result = d.get("toolCallResult") or {}
            call = self.pending.pop(result.get("callId") or "", None)
            status = result.get("status", "")
            if self.turn:
                self.turn.done = False  # results went back; the model continues
            if status == "error" and self.turn:
                self.turn.tool_errors += 1
            if call and status in ("success", "") and call[0] in SHELL_TOOLS and _GIT_COMMIT.search(call[1].get("command", "")):
                call[2].committed = True
                call[2].commit_dirs.append(command_dir(call[1].get("command", ""), s.cwd))

    def _resumed(self, ts) -> Turn:
        self.turn = Turn(started=ts or self.s.updated, prompt="(resumed)", origin="system")
        self.s.turns.append(self.turn)
        return self.turn
