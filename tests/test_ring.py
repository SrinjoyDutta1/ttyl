"""Ring ring: who rings, when it stops, and the sound file."""

import wave
from datetime import datetime, timedelta, timezone

from ttyl.model import Session, Status
from ttyl.ring import FINISHED, NEEDS_YOU, Ringer, ring_sound


def mk(sid, status, age=timedelta(0)):
    return Session(agent="claude", id=sid, status=status, updated=datetime.now(timezone.utc) - age)


def quiet() -> Ringer:
    return Ringer(sound=False, notify=False)


def test_first_look_rings_only_for_fresh_prompts():
    r = quiet()
    fresh, stale, idle = mk("fresh", Status.WAITING), mk("stale", Status.WAITING, timedelta(days=40)), mk("idle", Status.IDLE)
    assert r.update([fresh, stale, idle]) == []  # nothing is *new* on the first look, so no noise
    assert (fresh.ringing, stale.ringing, idle.ringing) == (NEEDS_YOU, "", "")


def test_transitions_ring_and_answering_stops_it():
    r = quiet()
    a, b = mk("a", Status.BUSY), mk("b", Status.BUSY)
    r.update([a, b])
    a.status, b.status = Status.WAITING, Status.IDLE
    assert r.update([a, b]) == [a, b]
    assert (a.ringing, b.ringing) == (NEEDS_YOU, FINISHED)

    a.status = Status.BUSY  # you approved it in the terminal
    r.update([a, b])
    assert a.ringing == "" and b.ringing == FINISHED

    r.ack("b")  # you went to it through ttyl
    r.update([a, b])
    assert b.ringing == ""


def test_closing_the_terminal_stops_the_ring():
    r = quiet()
    a = mk("a", Status.BUSY)
    r.update([a])
    a.status = Status.IDLE
    r.update([a])
    a.status = Status.CLOSED
    r.update([a])
    assert a.ringing == ""


def test_ring_sound_is_a_real_wav(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = ring_sound()
    with wave.open(str(path)) as w:
        secs = w.getnframes() / w.getframerate()
    assert path.parent == tmp_path / "ttyl" and 1.0 < secs < 1.3
