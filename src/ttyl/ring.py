"""Ring ring: get your attention when an agent needs you or finishes.

A session starts ringing when it becomes blocked on you, or when it finishes a
turn. It keeps ringing until you go to it (or it gets busy again because you
answered in the terminal). New rings play a phone ring; only one ttyl process
makes noise at a time (the menu bar app, when it's running).

System notifications are posted by the menu bar app, natively, so clicking one
takes you to the session. (Posting them from here via osascript made macOS
attribute them to Script Editor, and clicking one opened Script Editor.)

Muting (`ttyl mute`, or the bell in the app) silences the sound only: rings
still show, flash and notify.
"""

from __future__ import annotations

import math
import os
import re
import struct
import subprocess
import sys
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import settings
from .model import Session, Status
from .state import default_path

NEEDS_YOU, FINISHED = "needs you", "finished"
STALE = timedelta(hours=1)  # a prompt that was already waiting this long before ttyl started doesn't ring
FOREVER = "forever"  # muted until you unmute
MORNING = 8  # "until tomorrow morning" ends at 8am local time


def _mute_end(how: str, now: datetime) -> str | None:
    """What goes in settings for `ttyl mute <how>`: 30m, 1h, morning, on (until you unmute) or off."""
    how = how.strip().lower()
    if how in ("off", "unmute"):
        return None
    if how in ("on", FOREVER):
        return FOREVER
    if how == "morning":
        local = now.astimezone()
        end = local.replace(hour=MORNING, minute=0, second=0, microsecond=0)
        if end <= local:
            end += timedelta(days=1)
        return end.astimezone(timezone.utc).isoformat()
    m = re.fullmatch(r"(\d+)\s*(m|min|h|hr|hours?)", how)
    if m:
        n = int(m.group(1))
        return (now + (timedelta(minutes=n) if m.group(2).startswith("m") else timedelta(hours=n))).isoformat()
    raise ValueError(f"how long is {how!r}? try 30m, 1h, morning, on or off")


def _until(end: datetime, now: datetime) -> str:
    local = end.astimezone()
    clock = local.strftime("%I:%M%p").lstrip("0").lower()
    if local.date() == now.astimezone().date():
        return f"until {clock}"
    return f"until tomorrow {clock}" if local.date() == (now.astimezone() + timedelta(days=1)).date() \
        else f"until {local:%b %d} {clock}"


def mute_state(now: datetime | None = None) -> tuple[bool, str]:
    """(muted?, for how long: "until 8:00am" / "until you unmute")."""
    value = settings.get("muted_until")
    if not value:
        return False, ""
    if value == FOREVER:
        return True, "until you unmute"
    try:
        end = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False, ""
    now = now or datetime.now(timezone.utc)
    return (True, _until(end, now)) if end > now else (False, "")


def muted() -> bool:
    return mute_state()[0]


def set_mute(how: str, now: datetime | None = None) -> str:
    """Mute or unmute; says what happened. Raises ValueError for a duration it can't read."""
    now = now or datetime.now(timezone.utc)
    settings.set("muted_until", _mute_end(how, now))
    on, until = mute_state(now)
    return f"🔕 ring muted {until}: rings still show, just silently" if on else "🔔 ring unmuted"


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


def _play() -> None:
    try:
        subprocess.Popen(["afplay", str(ring_sound())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def alert_sound() -> None:
    """A different sound from the phone ring: something's wrong, not someone's calling."""
    if sys.platform != "darwin" or os.environ.get("TTYL_SOUND", "1") == "0" or muted() or someone_else_rings():
        return
    try:
        subprocess.Popen(["afplay", "/System/Library/Sounds/Funk.aiff"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class Ringer:
    def __init__(self, sound: bool = True):
        self.sound = sound and os.environ.get("TTYL_SOUND", "1") != "0" and sys.platform == "darwin"
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
        if self.sound and not muted() and not someone_else_rings():
            _play()


def ring_event(s: Session, reason: str = "") -> dict:
    """What the menu bar app needs to post a notification for a new ring."""
    reason = reason or s.ringing
    last = s.last_turn
    if reason == NEEDS_YOU:
        if last and last.last_action and "permission" in s.waiting_for:
            what = f"approve {last.last_action}"
        else:
            what = s.waiting_for or "waiting for you"
    else:
        what = s.title
    return {"id": s.id, "project": s.project, "title": s.title, "reason": reason, "what": what}
