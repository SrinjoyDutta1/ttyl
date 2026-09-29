"""Agent-neutral model: a Session is a lane, a Turn is one square in it."""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class Kind(str, Enum):
    """What a turn's square means, most informative first."""

    ACTIVE = "active"  # still running
    COMMIT = "commit"  # made a git commit
    FAIL = "fail"  # interrupted or errored out
    EDIT = "edit"  # changed files
    RUN = "run"  # used tools (shell, reads, search) but changed nothing
    CHAT = "chat"  # pure conversation


class Status(str, Enum):
    WAITING = "waiting"  # blocked on you (permission prompt, question)
    BUSY = "busy"  # agent is working
    IDLE = "idle"  # process alive, turn finished: your move
    CLOSED = "closed"  # no process attached


STATUS_RANK = {Status.WAITING: 0, Status.BUSY: 1, Status.IDLE: 2, Status.CLOSED: 3}


@dataclass
class Commit:
    sha: str
    subject: str
    when: datetime


@dataclass
class Turn:
    started: datetime
    prompt: str
    origin: str = "human"  # human | command | system
    ended: datetime | None = None
    done: bool = False
    had_agent: bool = False  # the agent responded at all
    tools: Counter = field(default_factory=Counter)
    files: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    tool_errors: int = 0
    interrupted: bool = False
    api_error: bool = False
    committed: bool = False  # ran a successful `git commit`
    commit_dirs: list[str] = field(default_factory=list)  # repos it committed in (from `cd X` / `git -C X`)
    commits: list[Commit] = field(default_factory=list)
    reply: str = ""  # last thing the agent said
    last_action: str = ""  # last tool call, human readable

    @property
    def kind(self) -> Kind:
        if not self.done:
            return Kind.ACTIVE
        if self.commits or self.committed:
            return Kind.COMMIT
        if self.interrupted or self.api_error:
            return Kind.FAIL
        if self.files:
            return Kind.EDIT
        if self.tools:
            return Kind.RUN
        return Kind.CHAT

    def add_file(self, path: str) -> None:
        if path and path not in self.files:
            self.files.append(path)


@dataclass
class Session:
    agent: str  # "claude" | "codex"
    id: str
    path: Path | None = None
    cwd: str = ""
    branch: str = ""
    entrypoint: str = ""  # cli, desktop, sdk-cli, ...
    ai_title: str = ""
    custom_title: str = ""
    started: datetime | None = None
    updated: datetime | None = None
    turns: list[Turn] = field(default_factory=list)
    away_summary: str = ""
    away_at: datetime | None = None
    imported: bool = False  # a copy of another agent's session (Codex imports)

    # live state, filled in by the store
    status: Status = Status.CLOSED
    waiting_for: str = ""
    pid: int | None = None
    tty: str = ""  # "ttys005", "" when none
    live_name: str = ""
    ringing: str = ""  # "needs you" / "finished" while it's trying to get your attention (ring.py)
    pending_since: datetime | None = None  # a tool call has been waiting for its result since (Codex)
    approval_mode: str = ""  # Codex: "on-request", "untrusted", "never", ...
    archived: bool = False  # hidden from the map (by ttyl, or archived in Codex)

    # remembered across runs (state.py)
    closed_at: datetime | None = None  # last time its process was seen alive
    launch_args: list[str] = field(default_factory=list)  # flags to reopen it with
    summary: str = ""  # model-written recap
    summary_turns: int = 0  # how many turns that recap covers
    summary_at: datetime | None = None

    @property
    def last_active(self) -> datetime | None:
        times = [t for t in (self.updated, self.closed_at) if t]
        return max(times) if times else None

    @property
    def visible_turns(self) -> list[Turn]:
        # slash commands like /model or /clear that the agent never answered are noise
        return [t for t in self.turns if t.origin != "command" or t.had_agent]

    @property
    def title(self) -> str:
        if self.custom_title:
            return self.custom_title
        if self.ai_title:
            return self.ai_title
        for t in self.visible_turns:
            if t.origin == "human":
                return one_line(t.prompt, 60)
        return "(nothing asked yet)"

    @property
    def project(self) -> str:
        if not self.cwd:
            return "?"
        home = os.path.expanduser("~")
        if self.cwd.rstrip("/") == home:
            return "~"
        return os.path.basename(self.cwd.rstrip("/"))

    @property
    def last_turn(self) -> Turn | None:
        turns = self.visible_turns
        return turns[-1] if turns else None

    @property
    def files(self) -> list[str]:
        seen: dict[str, None] = {}
        for t in self.turns:
            for f in t.files:
                seen.pop(f, None)
                seen[f] = None  # most recently touched last
        return list(seen)

    @property
    def commits(self) -> list[Commit]:
        return [c for t in self.turns for c in t.commits]

    def rel(self, path: str) -> str:
        if self.cwd and path.startswith(self.cwd.rstrip("/") + "/"):
            return path[len(self.cwd.rstrip("/")) + 1 :]
        return path.replace(os.path.expanduser("~"), "~", 1)


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


_WS = re.compile(r"\s+")
_PASTE = re.compile(r"<pasted_content[^>]*>(.*?)(?:</pasted_content[^>]*>|$)", re.S)
_HARNESS = re.compile(r"<(system-reminder|ide_opened_file|ide_selection)>.*?</\1>", re.S)
_MD = re.compile(r"\*\*|__|`")
_RECAP_HINT = re.compile(r"\s*\(disable recaps in /config\)\s*$")


def clean_prompt(text: str) -> str:
    """Drop the wrappers a harness puts around what you typed: pastes become [pasted]."""
    text = _HARNESS.sub("", text or "")
    pastes = _PASTE.findall(text)
    own = _PASTE.sub(" [pasted] ", text).strip()
    if pastes and own.replace("[pasted]", "").strip() == "":
        return "[pasted] " + pastes[0].strip()
    return own


def plain(text: str) -> str:
    """Markdown-ish reply -> plain text for one-line display."""
    return _RECAP_HINT.sub("", _MD.sub("", text or ""))


def one_line(text: str, width: int = 80) -> str:
    text = _WS.sub(" ", text or "").strip()
    return text if len(text) <= width else text[: width - 1] + "…"


_CD_PREFIX = re.compile(r"^\s*cd\s+(\"[^\"]*\"|'[^']*'|\S+)\s*(&&|;)\s*")
_GIT_C = re.compile(r"\bgit\s+-C\s+(\"[^\"]*\"|'[^']*'|\S+)")


def command_dir(cmd: str, cwd: str) -> str:
    """Where a shell command runs git: `git -C X` beats a leading `cd X`, else cwd."""
    m = _GIT_C.search(cmd or "") or _CD_PREFIX.match(cmd or "")
    if not m:
        return cwd
    path = os.path.expanduser(m.group(1).strip("\"'"))
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(cwd or "/", path))


def short_command(cmd: str) -> str:
    """`cd /repo && pytest -q` -> `pytest -q`, first line only."""
    cmd = (cmd or "").strip()
    while True:
        m = _CD_PREFIX.match(cmd)
        if not m:
            break
        cmd = cmd[m.end() :]
    return cmd.splitlines()[0] if cmd else ""
