"""Codex rollouts (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl) -> Session.

Same incremental contract as ClaudeParser: feed decoded events in order.
Covers the CLI and Desktop formats: `shell`/`exec_command` function calls,
`apply_patch` (function or freeform), and the Desktop app's JS `exec` tool.
"""

from __future__ import annotations

import json
import re

from .model import Session, Turn, clean_prompt, command_dir, one_line, parse_ts, short_command

SHELL_CALLS = {"shell", "container.exec", "exec_command", "local_shell", "shell_command"}

_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)
_JS_CMD = re.compile(r'exec_command\(\{\s*"cmd"\s*:\s*"((?:[^"\\]|\\.)*)"')
_EXIT = re.compile(r"(?:Process exited with code|Exit code:|\"exit_code\":)\s*(-?\d+)")
_GIT_COMMIT = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+commit\b")


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
        return out if isinstance(out, dict) else {}
    except (TypeError, ValueError):
        return {}


def _command(args: dict) -> str:
    cmd = args.get("cmd") or args.get("command") or ""
    if isinstance(cmd, list):
        # ["bash", "-lc", "pytest -q"] -> the script itself
        cmd = cmd[-1] if len(cmd) >= 3 and cmd[1] in ("-lc", "-c") else " ".join(cmd)
    return str(cmd)


def _output_text(out) -> str:
    if isinstance(out, str):
        return out
    if isinstance(out, list):
        return "\n".join(b.get("text", "") for b in out if isinstance(b, dict))
    if isinstance(out, dict):
        return str(out.get("output", ""))
    return ""


class CodexParser:
    def __init__(self, session: Session):
        self.s = session
        self.turn: Turn | None = None
        self.pending: dict[str, tuple[list[str], Turn]] = {}  # call_id -> commands

    def feed(self, d: dict) -> None:
        s = self.s
        ts = parse_ts(d.get("timestamp"))
        if ts:
            s.started = s.started or ts
            s.updated = ts
        p = d.get("payload") or {}
        kind, ptype = d.get("type"), p.get("type")

        if kind == "session_meta":
            s.id = p.get("id") or s.id
            s.cwd = p.get("cwd") or s.cwd
            origin = p.get("originator") or ""
            s.entrypoint = "desktop" if "Desktop" in origin else "cli"
            git = p.get("git") or {}
            s.branch = git.get("branch") or s.branch
        elif kind == "turn_context":
            s.cwd = p.get("cwd") or s.cwd
        elif kind == "event_msg":
            self._event(ptype, p, ts)
        elif kind == "response_item":
            self._item(ptype, p, ts)

    def _start(self, prompt: str, ts) -> Turn:
        if self.turn:
            self.turn.done = True
        self.turn = Turn(started=ts or self.s.updated, prompt=clean_prompt(prompt))
        self.s.turns.append(self.turn)
        return self.turn

    def _current(self, ts) -> Turn:
        return self.turn or self._start("(resumed)", ts)

    def _event(self, ptype, p: dict, ts) -> None:
        if ptype == "task_started":
            if str(p.get("turn_id", "")).startswith("external-import"):
                self.s.imported = True
        elif ptype == "user_message":
            text = (p.get("message") or "").strip() or ("[image]" if p.get("images") or p.get("local_images") else "")
            if text:
                self._start(text, ts)
        elif ptype == "agent_message":
            t = self._current(ts)
            t.had_agent = True
            t.ended = ts or t.ended
            if (p.get("message") or "").strip():
                t.reply = p["message"].strip()
        elif ptype == "task_complete":
            if self.turn:
                self.turn.done = True
                self.turn.ended = ts or self.turn.ended
                if (p.get("last_agent_message") or "").strip():
                    self.turn.reply = p["last_agent_message"].strip()
        elif ptype == "turn_aborted":
            if self.turn:
                self.turn.interrupted = True
                self.turn.done = True
                self.turn.ended = ts or self.turn.ended
        elif ptype == "patch_apply_end" and self.turn:
            for path in (p.get("changes") or {}):
                self.turn.add_file(path)

    def _item(self, ptype, p: dict, ts) -> None:
        if ptype in ("function_call", "custom_tool_call", "local_shell_call"):
            t = self._current(ts)
            t.had_agent = True
            t.ended = ts or t.ended
            name = p.get("name") or ptype
            cmds: list[str] = []
            if ptype == "local_shell_call":
                cmds = [_command(p.get("action") or {})]
            elif name in SHELL_CALLS:
                cmds = [_command(_args(p.get("arguments")))]
            elif name == "apply_patch":
                raw = p.get("input") or _args(p.get("arguments")).get("input", "")
                for f in _PATCH_FILE.findall(raw or ""):
                    t.add_file(f.strip())
                t.last_action = "✎ " + ", ".join(self.s.rel(f) for f in t.files[-2:])
            elif name == "exec":  # Desktop's JS tool: scrape the commands out of the script
                src = p.get("input") or ""
                cmds = [json.loads(f'"{m}"') for m in _JS_CMD.findall(src)]
                for f in _PATCH_FILE.findall(src.replace("\\n", "\n")):
                    t.add_file(f.strip())
            t.tools["shell" if cmds else name] += 1
            for c in cmds:
                t.commands.append(c)
                t.last_action = "$ " + one_line(short_command(c), 70)
            if p.get("call_id"):
                self.pending[p["call_id"]] = (cmds, t)
        elif ptype in ("function_call_output", "custom_tool_call_output"):
            call = self.pending.pop(p.get("call_id"), None)
            if not call:
                return
            cmds, t = call
            text = _output_text(p.get("output"))
            m = _EXIT.search(text)
            failed = bool(m and m.group(1) != "0")
            if failed:
                t.tool_errors += 1
            elif "nothing to commit" not in text:
                for c in cmds:
                    if _GIT_COMMIT.search(c):
                        t.committed = True
                        t.commit_dirs.append(command_dir(c, self.s.cwd))


def load_titles(index_path) -> dict[str, str]:
    """session_index.jsonl: {id, thread_name, updated_at} per line, last one wins."""
    titles: dict[str, str] = {}
    try:
        with open(index_path) as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("id") and row.get("thread_name"):
                    titles[row["id"]] = row["thread_name"]
    except OSError:
        pass
    return titles
