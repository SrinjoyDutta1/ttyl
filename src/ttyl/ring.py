"""Ring ring: get your attention when an agent needs you or finishes.

A session starts ringing when it becomes blocked on you, or when it finishes a
turn. It keeps ringing until you go to it (or it gets busy again because you
answered in the terminal). New rings play a phone ring and post a macOS
notification; only one ttyl process makes noise at a time (the menu bar app,
when it's running).
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .model import Session, Status
from .state import default_path

NEEDS_YOU, FINISHED = "needs you", "finished"
STALE = timedelta(hours=1)  # a prompt that was already waiting this long before ttyl started doesn't ring


def state_dir() -> Path:
    return default_path().parent


def ring_sound() -> Path:
    """A UK-style double ring (400 + 450 Hz, on 0.4s, off 0.2s, on 0.4s), generated once."""
    path = state_dir() / "ring.wav"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 22050
    frames = bytearray()
    for on, secs in ((True, 0.4), (False, 0.2), (True, 0.4), (False, 0.15)):
        for i in range(int(rate * secs)):
            t = i / rate
            sample = 0.0
            if on:
                fade = min(1.0, t / 0.01, (secs - t) / 0.01)  # no clicks at the edges
                sample = 0.35 * fade * (math.sin(2 * math.pi * 400 * t) + math.sin(2 * math.pi * 450 * t))
            frames += struct.pack("<h", int(sample * 32767 * 0.8))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def _lock() -> Path:
    return state_dir() / "ringer.pid"


def claim_noise() -> None:
    """The menu bar app calls this: while it runs, it's the one that rings."""
    try:
        _lock().parent.mkdir(parents=True, exist_ok=True)
        _lock().write_text(str(os.getpid()))
    except OSError:
        pass


def someone_else_rings() -> bool:
    try:
        pid = int(_lock().read_text().strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _notify(title: str, subtitle: str, body: str) -> None:
    script = "on run argv\n display notification (item 3 of argv) with title (item 1 of argv) subtitle (item 2 of argv)\nend run"
    try:
        subprocess.Popen(["osascript", "-e", script, title, subtitle, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _play() -> None:
    try:
        subprocess.Popen(["afplay", str(ring_sound())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class Ringer:
    def __init__(self, sound: bool = True, notify: bool = True):
        self.sound = sound and os.environ.get("TTYL_SOUND", "1") != "0" and sys.platform == "darwin"
        self.notify = notify and os.environ.get("TTYL_NOTIFY", "1") != "0" and sys.platform == "darwin"
        self.ringing: dict[str, str] = {}  # session id -> NEEDS_YOU | FINISHED
        self._last: dict[str, Status] = {}
        self._started = False

    def update(self, sessions: list[Session]) -> list[Session]:
        """Track transitions; returns sessions that just started ringing. Sets `s.ringing` on all of them."""
        now = datetime.now(timezone.utc)
        new: list[Session] = []
        for s in sessions:
            before = self._last.get(s.id)
            self._last[s.id] = s.status
            if s.status in (Status.BUSY, Status.CLOSED):
                self.ringing.pop(s.id, None)  # you answered it (or it's gone)
            elif s.status == Status.IDLE and self.ringing.get(s.id) == NEEDS_YOU:
                self.ringing.pop(s.id, None)
            if not self._started:
                if s.ringing:  # e.g. demo data
                    self.ringing[s.id] = s.ringing
                elif s.status == Status.WAITING and s.updated and now - s.updated < STALE:
                    self.ringing[s.id] = NEEDS_YOU
                continue
            if s.status == Status.WAITING and before != Status.WAITING:
                self.ringing[s.id] = NEEDS_YOU
                new.append(s)
            elif s.status == Status.IDLE and before == Status.BUSY:
                self.ringing[s.id] = FINISHED
                new.append(s)
        self._started = True
        for s in sessions:
            s.ringing = self.ringing.get(s.id, "")
        if new:
            self._make_noise(new)
        return new

    def ack(self, sid: str) -> None:
        self.ringing.pop(sid, None)

    def _make_noise(self, new: list[Session]) -> None:
        if someone_else_rings():
            return
        if self.sound:
            _play()
        if self.notify:
            for s in new[:3]:
                last = s.last_turn
                if s.ringing == NEEDS_YOU:
                    what = f"approve {last.last_action}" if last and last.last_action and "permission" in s.waiting_for else (s.waiting_for or "waiting for you")
                else:
                    what = s.title
                _notify("☎ ring ring", f"{s.project}: {s.ringing}", what)
