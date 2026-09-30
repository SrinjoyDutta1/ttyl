"""Collision warnings: two agent sessions editing the same file at around the same time.

Only a tool that sees every agent can notice this: Claude in one tab and Codex in
another both changing src/limits.py, each unaware of the other. A collision is
two or more sessions (at least one still open) that edited the same file within
WINDOW. The agents' own bookkeeping (memory files, plans) doesn't count.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .model import Session, Status

WINDOW = timedelta(minutes=60)
IGNORE = ("/.claude/", "/.codex/", "/.gemini/", "/.git/")


@dataclass
class Collision:
    path: str  # absolute
    sessions: list[Session] = field(default_factory=list)
    last: datetime | None = None

    @property
    def key(self) -> str:
        return self.path + "|" + ",".join(sorted(s.id for s in self.sessions))


def recent_edits(s: Session, now: datetime, window: timedelta = WINDOW) -> dict[str, datetime]:
    """Absolute path -> when this session last edited it, for edits within the window."""
    out: dict[str, datetime] = {}
    for t in s.visible_turns:
        when = t.ended or t.started
        if not when or now - when > window:
            continue
        for f in t.files:
            path = f if os.path.isabs(f) else os.path.normpath(os.path.join(s.cwd or "/", f))
            if any(part in path for part in IGNORE):
                continue
            if path not in out or when > out[path]:
                out[path] = when
    return out


def find(sessions: list[Session], now: datetime | None = None, window: timedelta = WINDOW) -> list[Collision]:
    now = now or datetime.now(timezone.utc)
    touched: dict[str, list[tuple[Session, datetime]]] = {}
    for s in sessions:
        if s.archived:
            continue
        for path, when in recent_edits(s, now, window).items():
            touched.setdefault(path, []).append((s, when))
    found = []
    for path, hits in touched.items():
        if len({s.id for s, _ in hits}) < 2 or all(s.status == Status.CLOSED for s, _ in hits):
            continue
        found.append(Collision(path, [s for s, _ in hits], max(w for _, w in hits)))
    found.sort(key=lambda c: c.last, reverse=True)
    return found


def annotate(sessions: list[Session], now: datetime | None = None) -> list[Collision]:
    """Find collisions and tell each session which of its files collide, and with whom."""
    collisions = find(sessions, now)
    for s in sessions:
        s.collisions = []
    for c in collisions:
        for s in c.sessions:
            others = [o for o in c.sessions if o is not s]
            s.collisions.append((c.path, others))
    return collisions


def describe(s: Session, other: Session) -> str:
    where = other.tty or ("codex app" if other.agent == "codex" and other.entrypoint == "desktop" else "")
    return f"{other.agent} · {other.title}" + (f" ({where})" if where else "")
