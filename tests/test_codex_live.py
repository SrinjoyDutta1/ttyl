"""Codex Desktop threads: live status from the rollout + the app process, names from its thread DB."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ttyl import procs as proclib
from ttyl.model import Status
from ttyl.store import Paths, Store
from ttyl.terminal import resume_command


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def rollout(path, tid, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


def ev(kind, payload, at):
    return {"timestamp": iso(at), "type": kind, "payload": payload}


def meta(tid, at):
    return ev("session_meta", {"id": tid, "cwd": "/work/app", "originator": "Codex Desktop"}, at)


@pytest.fixture
def home(tmp_path, monkeypatch):
    codex = tmp_path / "codex"
    paths = Paths(claude_projects=tmp_path / "claude" / "projects", claude_registry=tmp_path / "claude" / "sessions",
                  codex_sessions=codex / "sessions", codex_index=codex / "session_index.jsonl", codex_home=codex)
    paths.claude_registry.mkdir(parents=True)
    procs = {}
    monkeypatch.setattr(proclib, "snapshot", lambda: procs)
    codex.mkdir(parents=True)
    db = sqlite3.connect(codex / "state_5.sqlite")
    db.execute("create table threads (id text primary key, title text, name text, git_branch text, "
               "archived integer, approval_mode text)")
    db.commit()
    return paths, procs, db


def app_is_open(procs):
    procs[900] = proclib.Proc(900, 1, "", "/Applications/ChatGPT.app/Contents/Resources/codex -c x app-server")


def test_desktop_thread_rings_through_its_life(home):
    paths, procs, db = home
    now = datetime.now(timezone.utc)
    db.execute("insert into threads values ('t1', 'Fix login', '', 'feat/login', 0, 'on-request')")
    db.commit()
    f = paths.codex_sessions / "2026" / "09" / "29" / "rollout-t1.jsonl"
    events = [meta("t1", now - timedelta(minutes=5)),
              ev("turn_context", {"cwd": "/work/app", "approval_policy": "on-request"}, now - timedelta(minutes=5)),
              ev("event_msg", {"type": "task_started", "turn_id": "u1"}, now - timedelta(minutes=5)),
              ev("event_msg", {"type": "user_message", "message": "fix the login bug"}, now - timedelta(minutes=5))]
    rollout(f, "t1", events)
    store = Store(paths, days=3)

    app_is_open(procs)
    (s,) = store.refresh()
    assert (s.title, s.branch, s.status) == ("Fix login", "feat/login", Status.BUSY)

    # a shell call with no result for a minute, under an approval policy: probably asking you
    events.append(ev("response_item", {"type": "function_call", "name": "exec_command", "call_id": "c1",
                                       "arguments": json.dumps({"cmd": "rm -rf build"})}, now - timedelta(minutes=1)))
    rollout(f, "t1", events)
    (s,) = store.refresh()
    assert s.status == Status.WAITING and s.waiting_for == "approval (probably)"

    events += [ev("response_item", {"type": "function_call_output", "call_id": "c1", "output": "Exit code: 0"}, now),
               ev("event_msg", {"type": "task_complete", "turn_id": "u1", "last_agent_message": "Fixed."}, now)]
    rollout(f, "t1", events)
    (s,) = store.refresh()
    assert s.status == Status.IDLE and s.last_turn.reply == "Fixed."

    del procs[900]  # quit the Codex app
    (s,) = store.refresh()
    assert s.status == Status.CLOSED
    assert resume_command(s) == "open codex://threads/t1"


def test_archived_in_codex_is_archived_here(home):
    paths, _, db = home
    now = datetime.now(timezone.utc)
    db.execute("insert into threads values ('t2', 'Old thing', '', '', 1, 'never')")
    db.commit()
    rollout(paths.codex_sessions / "2026" / "09" / "29" / "rollout-t2.jsonl", "t2",
            [meta("t2", now), ev("event_msg", {"type": "user_message", "message": "hi"}, now)])
    store = Store(paths, days=3)
    assert store.refresh() == []  # hidden from the map
    store.show_all = True
    (s,) = store.refresh()
    assert s.archived
