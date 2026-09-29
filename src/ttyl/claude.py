"""Claude Code transcripts (~/.claude/projects/<dir>/<session>.jsonl) -> Session.

The parser is incremental: feed it one decoded JSONL event at a time, in order,
and it keeps the Session up to date. The store tails files and feeds new lines.
"""

from __future__ import annotations

import re

from .model import Session, Turn, clean_prompt, command_dir, one_line, parse_ts, short_command

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

_GIT_COMMIT = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+commit\b")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _tag(text: str, name: str) -> str:
    """Inner text of <name>...</name>; tags nest, e.g. <task-notification><summary>."""
    m = re.search(rf"<{name}>(.*?)</{name}>", text, re.S)
    return m.group(1).strip() if m else ""


def describe_tool(name: str, inp: dict, rel=lambda p: p) -> str:
    inp = inp or {}
    if name == "Bash":
        return "$ " + one_line(short_command(inp.get("command", "")), 70)
    if name in EDIT_TOOLS:
        return "✎ " + rel(inp.get("file_path") or inp.get("notebook_path") or "")
    if name == "Read":
        return "read " + rel(inp.get("file_path", ""))
    if name in ("Grep", "Glob"):
        return f"search {one_line(inp.get('pattern', ''), 50)}"
    if name == "WebSearch":
        return "web: " + one_line(inp.get("query", ""), 60)
    if name == "WebFetch":
        return "fetch " + one_line(inp.get("url", ""), 60)
    if name in ("Agent", "Task"):
        return "subagent: " + one_line(inp.get("description", "") or inp.get("prompt", ""), 60)
    if name == "AskUserQuestion":
        qs = inp.get("questions") or [{}]
        return "asking you: " + one_line(qs[0].get("question", ""), 60)
    if name == "TodoWrite":
        return "updating todo list"
    return name


class ClaudeParser:
    def __init__(self, session: Session):
        self.s = session
        self.turn: Turn | None = None
        self.pending: dict[str, tuple[str, dict, Turn]] = {}  # tool_use_id -> call

    # -- events -----------------------------------------------------------

    def feed(self, d: dict) -> None:
        s = self.s
        ts = parse_ts(d.get("timestamp"))
        if ts:
            s.started = s.started or ts
            s.updated = ts
        if d.get("cwd"):
            s.cwd = d["cwd"]
        if d.get("gitBranch"):
            s.branch = d["gitBranch"]
        if d.get("entrypoint"):
            s.entrypoint = d["entrypoint"]
        if d.get("isSidechain"):
            return  # subagent chatter; the parent's Agent tool call already counts it

        kind = d.get("type")
        if kind == "user":
            self._user(d, ts)
        elif kind == "assistant":
            self._assistant(d, ts)
        elif kind == "ai-title":
            s.ai_title = d.get("aiTitle") or s.ai_title
        elif kind == "custom-title":
            s.custom_title = d.get("customTitle") or s.custom_title
        elif kind == "system":
            sub = d.get("subtype")
            if sub == "away_summary" and d.get("content"):
                s.away_summary, s.away_at = d["content"], ts
            elif sub == "turn_duration" and self.turn:
                self.turn.done = True
                self.turn.ended = ts or self.turn.ended

    def _start(self, prompt: str, ts, origin: str) -> Turn:
        prev = self.turn
        if prev:
            prev.done = True
            if prev.origin == "command" and not prev.had_agent:
                self.s.turns.remove(prev)
        self.turn = Turn(started=ts or self.s.updated, prompt=clean_prompt(prompt), origin=origin)
        self.s.turns.append(self.turn)
        return self.turn

    def _user(self, d: dict, ts) -> None:
        if d.get("isMeta") or d.get("isCompactSummary"):
            return
        content = (d.get("message") or {}).get("content")
        if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    self._tool_result(b, d.get("toolUseResult"))
            return

        text = _text_of(content).strip()
        if not text and isinstance(content, list) and content:
            text = "[image]"
        if not text:
            return
        if text.startswith("[Request interrupted"):
            if self.turn:
                self.turn.interrupted = True
                self.turn.done = True
                self.turn.ended = ts
            return
        if text.startswith(("<local-command-stdout>", "<local-command-stderr>", "<local-command-caveat>", "<bash-stdout>", "<bash-stderr>")):
            return

        origin = "human"
        if isinstance(d.get("origin"), dict) and d["origin"].get("kind") == "task-notification":
            origin = "system"
        if text.startswith("<task-notification>"):
            origin = "system"
            text = "⚙ " + (_tag(text, "summary") or "background task finished")
        elif text.startswith("<command-name>") or text.startswith("<command-message>"):
            origin = "command"
            text = f"{_tag(text, 'command-name')} {_tag(text, 'command-args')}".strip() or text
        elif text.startswith("<bash-input>"):
            origin = "command"
            text = "! " + _tag(text, "bash-input")
        self._start(text, ts, origin)

    def _assistant(self, d: dict, ts) -> None:
        msg = d.get("message") or {}
        turn = self.turn or self._start("(resumed)", ts, "system")
        turn.had_agent = True
        turn.ended = ts or turn.ended
        if d.get("isApiErrorMessage"):
            turn.api_error = True
        rel = self.s.rel
        for b in msg.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                turn.reply = b["text"].strip()
            elif b.get("type") == "tool_use":
                name, inp = b.get("name", "?"), b.get("input") or {}
                turn.tools[name] += 1
                turn.last_action = describe_tool(name, inp, rel)
                if name in EDIT_TOOLS:
                    turn.add_file(inp.get("file_path") or inp.get("notebook_path") or "")
                elif name == "Bash":
                    turn.commands.append(inp.get("command", ""))
                if b.get("id"):
                    self.pending[b["id"]] = (name, inp, turn)
        stop = msg.get("stop_reason")
        if stop in ("end_turn", "stop_sequence"):
            turn.done = True
        elif stop == "tool_use":
            turn.done = False

    def _tool_result(self, b: dict, extra) -> None:
        call = self.pending.pop(b.get("tool_use_id"), None)
        if not call:
            return
        name, inp, turn = call
        if b.get("is_error"):
            turn.tool_errors += 1
            return
        if name == "Bash" and _GIT_COMMIT.search(inp.get("command", "")):
            out = extra.get("stdout", "") if isinstance(extra, dict) else _text_of(b.get("content"))
            if "nothing to commit" not in out:
                turn.committed = True
                turn.commit_dirs.append(command_dir(inp.get("command", ""), self.s.cwd))
