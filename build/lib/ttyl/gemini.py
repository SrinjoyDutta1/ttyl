"""Gemini CLI sessions (~/.gemini/tmp/<project>/chats/session-*.jsonl) -> Session.

Format (gemini-cli >= 0.39, packages/core/src/services/chatRecording*.ts):
line 1 is metadata {sessionId, projectHash, startTime, lastUpdated, kind}; then
message records {id, timestamp, type: user|gemini|info|error|warning, content,
toolCalls?}. A message is re-appended whole whenever it changes (the last copy of
an id wins), `{"$set": {...}}` updates metadata (a `messages` key there replaces
every message: a checkpoint), and `{"$rewindTo": id}` drops that message and all
after it. Older versions (< 0.39) wrote one JSON object with a `messages` array;
the store feeds those as a single `{"$legacy": obj}` event.

Gemini never writes "awaiting approval" or an end-of-turn marker to disk, so a turn
counts as done once the model has answered in text with no tool calls after it.
"""

from __future__ import annotations

import os
import re

from .model import Session, Turn, command_dir, one_line, parse_ts, short_command

EDIT_TOOLS = {"replace", "write_file", "edit"}
SHELL_TOOLS = {"run_shell_command"}
IGNORED_PREFIXES = ("/", "?", "<session_context>", "<hook_context>")  # upstream isIgnoredUserContent

_GIT_COMMIT = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+commit\b")


def parts_text(content) -> str:
    """Gemini `content` is a string or a list of genai Parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("text"))
    return ""


def is_tool_response(content) -> bool:
    return isinstance(content, list) and any(isinstance(p, dict) and "functionResponse" in p for p in content)


class GeminiParser:
    def __init__(self, session: Session):
        self.s = session
        self.messages: dict[str, dict] = {}  # id -> latest copy, in first-seen order
        self._dirty = False

    def feed(self, d: dict) -> None:
        if "$legacy" in d:  # a whole pre-0.39 session file
            obj = d["$legacy"] or {}
            self._meta(obj)
            self.messages = {m.get("id") or str(i): m for i, m in enumerate(obj.get("messages") or [])}
        elif "$set" in d:
            upd = d["$set"] or {}
            if "messages" in upd:  # checkpoint: replaces everything
                self.messages = {m.get("id") or str(i): m for i, m in enumerate(upd["messages"] or [])}
            self._meta(upd)
        elif "$rewindTo" in d:
            ids = list(self.messages)
            if d["$rewindTo"] in ids:
                for mid in ids[ids.index(d["$rewindTo"]):]:
                    del self.messages[mid]
        elif "sessionId" in d and "type" not in d:
            self._meta(d)
        elif d.get("id") and d.get("type"):
            self.messages[d["id"]] = d
        self._dirty = True

    def _meta(self, m: dict) -> None:
        s = self.s
        if m.get("sessionId"):
            s.id = m["sessionId"]
        if m.get("summary"):
            s.ai_title = one_line(m["summary"], 80)
        if m.get("kind") == "subagent":
            s.imported = True  # a subagent's own log; the parent session covers it
        for key, attr in (("startTime", "started"), ("lastUpdated", "updated")):
            ts = parse_ts(m.get(key))
            if ts and (attr == "updated" or getattr(s, attr) is None):
                setattr(s, attr, ts)

    def flush(self) -> None:
        """Rebuild turns from the current messages (called after each batch of lines)."""
        if not self._dirty:
            return
        self._dirty = False
        s = self.s
        s.turns = []
        turn: Turn | None = None
        pending_text_answer = False
        for m in self.messages.values():
            ts = parse_ts(m.get("timestamp"))
            if ts and (s.updated is None or ts > s.updated):
                s.updated = ts
            kind, content = m.get("type"), m.get("content")
            if kind == "user":
                if is_tool_response(content):
                    if turn:
                        turn.done = False  # tool results went back; the model is thinking again
                    continue
                text = parts_text(content).strip()
                if not text or text.startswith(IGNORED_PREFIXES):
                    continue
                if turn:
                    turn.done = True
                turn = Turn(started=ts or s.updated, prompt=text)
                s.turns.append(turn)
            elif kind == "gemini":
                if turn is None:
                    turn = Turn(started=ts or s.updated, prompt="(resumed)", origin="system")
                    s.turns.append(turn)
                turn.had_agent = True
                turn.ended = ts or turn.ended
                text = parts_text(content).strip()
                calls = m.get("toolCalls") or []
                if text:
                    turn.reply = text
                for call in calls:
                    self._tool(turn, call)
                turn.done = bool(text) and not calls
            elif kind == "error" and turn:
                turn.api_error = True
        if turn and turn.interrupted:
            turn.done = True

    def _tool(self, turn: Turn, call: dict) -> None:
        name, args = call.get("name", "?"), call.get("args") or {}
        status = call.get("status", "")
        turn.tools[name] += 1
        if name in EDIT_TOOLS:
            display = call.get("resultDisplay") if isinstance(call.get("resultDisplay"), dict) else {}
            path = display.get("filePath") or args.get("file_path") or args.get("absolute_path") or ""
            if path and not os.path.isabs(path) and self.s.cwd:
                path = os.path.normpath(os.path.join(self.s.cwd, path))
            if status != "cancelled":
                turn.add_file(path)
            turn.last_action = "✎ " + self.s.rel(path)
        elif name in SHELL_TOOLS:
            cmd = args.get("command", "")
            turn.commands.append(cmd)
            turn.last_action = "$ " + one_line(short_command(cmd), 70)
            if status == "success" and _GIT_COMMIT.search(cmd):
                turn.committed = True
                turn.commit_dirs.append(command_dir(cmd, args.get("dir_path") or self.s.cwd))
        else:
            turn.last_action = name
        if status == "error":
            turn.tool_errors += 1
        elif status == "cancelled":
            turn.interrupted = True
