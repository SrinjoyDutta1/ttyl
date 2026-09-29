"""`ttyl serve`: the engine behind the menu bar app.

Prints one JSON snapshot per line on stdout (whenever something changes, and
every few seconds regardless), and takes one JSON command per line on stdin:

    {"cmd": "go", "id": "<session>"}       jump to its terminal, or reopen it if closed
    {"cmd": "ack", "id": "<session>"}      stop it ringing
    {"cmd": "all", "on": true}             include all history
    {"cmd": "refresh"}
    {"cmd": "open_terminal_view"}          open the full TUI in a terminal window
    {"cmd": "test_ring"}                   ring once for the top session (checks sound + notifications)

A snapshot's "rings" lists sessions that started ringing since the last one; the
app posts a notification for each (clicking it sends "go").

Exits when stdin closes, so it never outlives the app that started it.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from . import render, terminal
from .model import Session, Status, one_line, plain
from .ring import Ringer, _play, claim_noise, ring_event, state_dir
from .summarize import Summarizer, enabled_by_env, load_key_from_shell

TURNS = 14
HEARTBEAT = 10.0


def log(line: str) -> None:
    """~/.local/state/ttyl/app.log, shared with the menu bar app."""
    try:
        with open(state_dir() / "app.log", "a") as fh:
            fh.write(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} engine: {line}\n")
    except OSError:
        pass


def _turn(s: Session, t) -> dict:
    glyph, _ = render.glyph(s, t)
    kind = "waiting" if glyph == render.WAITING_GLYPH[0] else t.kind.value
    return {
        "kind": kind,
        "time": render.clock(t.started).strip(),
        "prompt": one_line(t.prompt, 140),
        "files": len(t.files),
        "commits": [f"{c.sha} {one_line(c.subject, 80)}" for c in t.commits],
    }


def session_json(s: Session, number: int | None) -> dict:
    act = render.action(s)
    replied = [t for t in s.visible_turns if t.reply]
    recap = plain(s.away_summary).strip() if s.away_summary else (
        render._first_lines(replied[-1].reply, 3, 300) if replied else "")
    humans = [t for t in s.visible_turns if t.origin == "human"]
    turns = s.visible_turns
    return {
        "id": s.id,
        "number": number,
        "section": render.section_of(s).key,
        "title": s.title,
        "project": s.project,
        "branch": s.branch if s.branch != "HEAD" else "",
        "agent": s.agent,
        "status": s.status.value,
        "ringing": s.ringing,
        "where": render.where(s),
        "when": render.when(s),
        "action": act.plain if act is not None else "",
        "summary": s.summary,
        "recap": recap,
        "last_ask": one_line(humans[-1].prompt, 240) if humans else "",
        "last_reply": render._first_lines(replied[-1].reply, 2, 200) if replied else "",
        "turns": [_turn(s, t) for t in turns[-TURNS:]],
        "hidden_turns": max(0, len(turns) - TURNS),
        "resume_command": terminal.resume_command(s) if s.status == Status.CLOSED else "",
    }


def snapshot(sessions: list[Session], show_all: bool = False, notices: list[str] | None = None,
             rings: list[dict] | None = None) -> dict:
    groups = render.grouped(sessions)
    out_sessions, number = [], 0
    for _, members in groups:
        for s in members:
            number += 1
            out_sessions.append(session_json(s, number if number <= 9 else None))
    return {
        "type": "snapshot",
        "sections": [{"key": sec.key, "title": sec.title, "hint": sec.hint, "count": len(m)} for sec, m in groups],
        "sessions": out_sessions,
        "ringing": sum(bool(s.ringing) for s in sessions),
        "needs_you": sum(s.status == Status.WAITING for s in sessions),
        "show_all": show_all,
        "notices": notices or [],
        "rings": rings or [],
    }


class Engine:
    """The loop, separated from stdin/stdout so it can be tested."""

    def __init__(self, store, ring: bool = True, summaries: bool = True):
        self.store = store
        demo = getattr(store, "demo", False)
        self.ringer = Ringer(sound=ring and not demo)
        self.rings: list[dict] = []
        self.summarizer = Summarizer(getattr(store, "state", None)) if summaries and enabled_by_env() and not demo else None
        self.sessions: list[Session] = []
        self.notices: list[str] = []
        self._summarizing = False

    def tick(self) -> dict:
        self.sessions = self.store.refresh()
        self.rings += [ring_event(s) for s in self.ringer.update(self.sessions)]
        self._maybe_summarize()
        snap = snapshot(self.sessions, getattr(self.store, "show_all", False), self.notices, self.rings)
        self.notices, self.rings = [], []
        return snap

    def handle(self, cmd: dict) -> None:
        name = cmd.get("cmd")
        s = next((x for x in self.sessions if x.id == cmd.get("id")), None)
        if name == "all":
            self.store.show_all = bool(cmd.get("on"))
        elif name == "ack" and s:
            self.ringer.ack(s.id)
            s.ringing = ""
        elif name == "go" and s:
            self.ringer.ack(s.id)
            s.ringing = ""
            self.notices.append(self._go(s))
            if not getattr(self.store, "demo", False):
                log(f"go {s.project} ({s.tty or render.where(s) or s.status.value}): {self.notices[-1]}")
        elif name == "go" and not getattr(self.store, "demo", False):
            log(f"go {cmd.get('id')}: no such session")
        elif name == "open_terminal_view":
            terminal.run_in_new_window("ttyl")
        elif name == "test_ring":
            ordered = render.ordered(self.sessions)
            if ordered:
                top = ordered[0]
                self.rings.append(ring_event(top, top.ringing or ("needs you" if top.status == Status.WAITING else "finished")))
            if self.ringer.sound:
                _play()

    def _go(self, s: Session) -> str:
        """Bring a session's terminal forward (or reopen it); say what happened."""
        demo = getattr(self.store, "demo", False)
        if s.status == Status.CLOSED:
            if demo:
                return f"demo: would reopen with {terminal.resume_command(s)}"
            return f"reopening {s.project} in a new window" if terminal.reopen(s) else \
                f"couldn't open a terminal: {terminal.last_error}"
        if s.tty:
            if demo:
                return f"demo: would bring {s.tty} to the front"
            return f"→ {s.tty}" if terminal.focus(s.tty) else f"couldn't go to {s.tty}: {terminal.last_error}"
        if render.where(s) == "bg":
            return f"{s.project} is a background Claude session: it has no terminal tab to go to"
        return f"{s.project} runs in {render.where(s) or 'no terminal'}, nothing to go to"

    def _maybe_summarize(self) -> None:
        if self.summarizer is None or self._summarizing:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=getattr(self.store, "days", 3))
        s = self.summarizer.pick(render.ordered(self.sessions), None, cutoff)
        if s is None:
            return
        self._summarizing = True

        def run():
            try:
                self.summarizer.summarize(s)
            finally:
                self._summarizing = False

        threading.Thread(target=run, daemon=True).start()


def serve(store, interval: float = 2.0, once: bool = False, ring: bool = True, summaries: bool = True,
          out=None, inp=None) -> int:
    out = out or sys.stdout
    inp = inp or sys.stdin
    if summaries and not once and not getattr(store, "demo", False) and not sys.stdin.isatty():
        load_key_from_shell()  # launched by the menu bar app, without your shell's environment
    engine = Engine(store, ring=ring, summaries=summaries)
    if ring and not getattr(store, "demo", False):
        claim_noise()  # while the menu bar app runs, it's the one that rings
    if not once and not getattr(store, "demo", False) and sys.platform == "darwin":
        def check():  # also makes macOS ask for permission now rather than on your first click
            ok, detail = terminal.can_control_terminal()
            log(f"terminal control: {'ok' if ok else 'FAILED'} ({detail})")
        threading.Thread(target=check, daemon=True).start()

    commands: queue.Queue = queue.Queue()
    closed = threading.Event()

    def read():
        for line in inp:
            try:
                commands.put(json.loads(line))
            except ValueError:
                continue
        closed.set()
        commands.put(None)

    if not once:
        threading.Thread(target=read, daemon=True).start()

    last, sent_at = None, 0.0
    stop = False
    while True:
        snap = engine.tick()
        body = json.dumps(snap, sort_keys=True)
        if body != last or time.monotonic() - sent_at > HEARTBEAT:
            out.write(body + "\n")
            out.flush()
            last, sent_at = body, time.monotonic()
        if once or stop:
            return 0
        try:
            batch = [commands.get(timeout=interval)]
        except queue.Empty:
            continue
        while not commands.empty():  # a burst of clicks
            batch.append(commands.get_nowait())
        for cmd in batch:
            if cmd is None:  # stdin closed: the app that started us is gone
                stop = True
            else:
                engine.handle(cmd)
