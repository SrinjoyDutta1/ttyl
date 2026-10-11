"""Ring ring: who rings, when it stops, and the sound file."""

import wave
from datetime import datetime, timedelta, timezone

import pytest

from ttyl.model import Session, Status
from ttyl.ring import FINISHED, NEEDS_YOU, Ringer, ring_sound


def mk(sid, status, age=timedelta(0)):
    return Session(agent="claude", id=sid, status=status, updated=datetime.now(timezone.utc) - age)


def quiet() -> Ringer:
    return Ringer(sound=False)


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


def test_mute_durations():
    from ttyl.ring import FOREVER, _mute_end

    now = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)
    assert datetime.fromisoformat(_mute_end("1h", now)) == now + timedelta(hours=1)
    assert datetime.fromisoformat(_mute_end("30m", now)) == now + timedelta(minutes=30)
    assert datetime.fromisoformat(_mute_end("2 hours", now)) == now + timedelta(hours=2)
    assert _mute_end("on", now) == FOREVER and _mute_end("off", now) is None
    morning = datetime.fromisoformat(_mute_end("morning", now)).astimezone()
    assert morning.hour == 8 and morning.minute == 0
    assert timedelta(0) < morning - now <= timedelta(days=1)  # the next 8am, never one that's passed
    with pytest.raises(ValueError):
        _mute_end("a while", now)


def test_mute_runs_out():
    from ttyl.ring import mute_state, set_mute

    now = datetime.now(timezone.utc)
    assert mute_state() == (False, "")
    assert "muted until" in set_mute("1h", now)
    assert mute_state(now)[0] and not mute_state(now + timedelta(hours=2))[0]
    set_mute("on", now)
    assert mute_state(now + timedelta(days=30)) == (True, "until you unmute")
    assert set_mute("off") == "🔔 ring unmuted" and mute_state() == (False, "")


def test_muted_rings_still_ring_but_make_no_sound(monkeypatch):
    from ttyl import ring
    from ttyl.ring import set_mute

    played = []
    monkeypatch.setattr(ring, "_play", lambda: played.append(1))
    monkeypatch.setattr(ring, "someone_else_rings", lambda: False)
    r = Ringer(sound=False)
    r.sound = True  # as on a Mac, without touching the real speaker (_play is faked above)
    a = mk("a", Status.BUSY)
    r.update([a])
    set_mute("on")
    a.status = Status.IDLE
    assert r.update([a]) == [a] and a.ringing == FINISHED and played == []
    set_mute("off")
    a.status = Status.BUSY
    r.update([a])
    a.status = Status.IDLE
    r.update([a])
    assert played == [1]
