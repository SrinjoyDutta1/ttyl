"""What ttyl remembers between runs, in ~/.local/state/ttyl/state.json.

Per session: where it lived, the flags it was launched with, the last time its
process was seen alive, and its saved summary. That's what lets a terminal you
closed by accident (even one idle for weeks) stay on the map and reopen as it was.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .model import Session

SAVE_EVERY = 15.0  # seconds; live timestamps change every refresh, the disk doesn't need to


def default_path() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"))
    old, new = base / "agentterm", base / "ttyl"
    if old.is_dir() and not new.exists():
        old.rename(new)  # this project used to be called agentterm
    return new / "state.json"


class State:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_path()
        self.sessions: dict[str, dict] = {}
        self._dirty = False
        self._saved_at = 0.0
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data.get("sessions"), dict):
                self.sessions = data["sessions"]
        except (OSError, ValueError, AttributeError):
            pass

    def get(self, sid: str) -> dict:
        return self.sessions.get(sid, {})

    def seen_live(self, s: Session, args: list[str], now: float | None = None) -> None:
        rec = self.sessions.setdefault(s.id, {})
        fields = {"agent": s.agent, "cwd": s.cwd, "title": s.title, "tty": s.tty, "args": args,
                  "path": str(s.path) if s.path else rec.get("path", "")}
        if any(rec.get(k) != v for k, v in fields.items()):
            rec.update(fields)
            self._dirty = True
        rec["last_live"] = now if now is not None else time.time()
        self._dirty = True

    def recently_live(self, cutoff: float) -> dict[str, dict]:
        return {sid: rec for sid, rec in self.sessions.items() if rec.get("last_live", 0) >= cutoff}

    def set_summary(self, sid: str, text: str, turns: int, model: str) -> None:
        rec = self.sessions.setdefault(sid, {})
        rec["summary"] = {"text": text, "turns": turns, "at": time.time(), "model": model}
        self.save(force=True)

    def set_archived(self, sid: str, on: bool) -> None:
        rec = self.sessions.setdefault(sid, {})
        if on:
            rec["archived_at"] = time.time()
        else:
            rec.pop("archived_at", None)
        self.save(force=True)

    def forget(self, sid: str) -> None:
        self.sessions.pop(sid, None)
        self.save(force=True)

    def save(self, force: bool = False) -> None:
        if not self._dirty and not force:
            return
        if not force and time.monotonic() - self._saved_at < SAVE_EVERY:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
            with os.fdopen(fd, "w") as fh:
                json.dump({"version": 1, "sessions": self.sessions}, fh, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            return
        self._dirty = False
        self._saved_at = time.monotonic()
