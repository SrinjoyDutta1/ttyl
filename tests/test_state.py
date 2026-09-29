"""Closed terminals stay on the map and reopen as they were."""

import json
import os

import pytest

from ttyl import procs as proclib
from ttyl.model import Session, Status
from ttyl.procs import launch_args
from ttyl.state import State
from ttyl.store import Paths, Store
from ttyl.terminal import resume_command

from helpers import Claude


@pytest.fixture
def home(tmp_path, monkeypatch):
    paths = Paths(
        claude_projects=tmp_path / "claude" / "projects",
        claude_registry=tmp_path / "claude" / "sessions",
        codex_sessions=tmp_path / "codex" / "sessions",
        codex_index=tmp_path / "codex" / "session_index.jsonl",
    )
    paths.claude_registry.mkdir(parents=True)
    procs = {}
    monkeypatch.setattr(proclib, "snapshot", lambda: procs)
    return paths, procs, tmp_path / "state.json"


def test_launch_args_keep_only_reopen_flags():
    assert launch_args("claude --dangerously-skip-permissions") == ["--dangerously-skip-permissions"]
    assert launch_args("claude --model opus --resume abc fix the bug") == ["--model", "opus"]
    assert launch_args("claude --permission-mode=plan -p hi") == ["--permission-mode=plan"]
    assert launch_args("claude") == []


def test_resume_command_restores_folder_and_flags():
    s = Session(agent="claude", id="abc", cwd="/work/my repo", launch_args=["--model", "opus"])
    assert resume_command(s) == "cd '/work/my repo' && claude --resume abc --model opus"
    assert resume_command(Session(agent="codex", id="x1", cwd="/w")) == "cd /w && codex resume x1"


def test_accidentally_closed_terminal_stays_and_reopens_as_it_was(home):
    paths, procs, state_file = home
    path = Claude().prompt("long idle work").say("done").write(paths.claude_projects / "-w" / "old.jsonl")
    os.utime(path, (1, 1))  # transcript untouched for ages, far outside the 3-day window

    procs[300] = proclib.Proc(300, 1, "ttys004", "claude --dangerously-skip-permissions")
    (paths.claude_registry / "300.json").write_text(json.dumps(
        {"pid": 300, "sessionId": "old", "cwd": "/w", "status": "idle", "kind": "interactive"}))

    store = Store(paths, days=3, state=State(state_file))
    (s,) = store.refresh()
    assert s.status == Status.IDLE and s.launch_args == ["--dangerously-skip-permissions"]

    del procs[300]  # oops, closed the tab
    (s,) = store.refresh()
    assert s.status == Status.CLOSED and s.closed_at is not None
    assert resume_command(s).endswith("claude --resume old --dangerously-skip-permissions")

    store.state.save(force=True)
    (again,) = Store(paths, days=3, state=State(state_file)).refresh()  # ttyl restarted
    assert again.id == "old" and again.status == Status.CLOSED and again.launch_args == s.launch_args


def test_saved_summary_is_reattached(home):
    paths, _, state_file = home
    Claude().prompt("x").say("y").write(paths.claude_projects / "-w" / "aaa.jsonl")
    State(state_file).set_summary("aaa", "It did the thing.", 1, "claude-opus-5-5")
    (s,) = Store(paths, days=10_000, state=State(state_file)).refresh()
    assert s.summary == "It did the thing." and s.summary_turns == 1 and s.summary_at is not None


def test_state_survives_a_corrupt_file(tmp_path):
    bad = tmp_path / "state.json"
    bad.write_text("{not json")
    st = State(bad)
    assert st.sessions == {}
    st.set_summary("a", "ok", 1, "m")
    assert json.loads(bad.read_text())["sessions"]["a"]["summary"]["text"] == "ok"


def test_archive_hides_until_new_activity(home):
    paths, _, state_file = home
    path = paths.claude_projects / "-w" / "aaa.jsonl"
    b = Claude().prompt("x").say("y")
    b.write(path)
    store = Store(paths, days=10_000, state=State(state_file))
    (s,) = store.refresh()
    store.archive(s)
    assert store.refresh() == []
    store.show_all = True
    (s,) = store.refresh()
    assert s.archived
    store.show_all = False

    import json as _json
    from datetime import datetime, timezone
    with open(path, "a") as fh:  # the session does something new: it comes back
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        fh.write(_json.dumps({"type": "user", "timestamp": now, "message": {"role": "user", "content": "more"}}) + "\n")
    (s,) = store.refresh()
    assert not s.archived and s.last_turn.prompt == "more"


def test_delete_moves_the_transcript_to_the_trash(home, tmp_path, monkeypatch):
    paths, procs, state_file = home
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    path = Claude().prompt("tidy up").say("ok").write(paths.claude_projects / "-w" / "gone.jsonl")
    (path.with_suffix("") / "subagents").mkdir(parents=True)
    (path.with_suffix("") / "subagents" / "agent-1.jsonl").write_text("{}\n")
    Claude().prompt("still open").say("ok").write(paths.claude_projects / "-w" / "live.jsonl")
    procs[500] = proclib.Proc(500, 1, "ttys001", "claude")
    (paths.claude_registry / "500.json").write_text(json.dumps({"pid": 500, "sessionId": "live", "status": "idle"}))

    store = Store(paths, days=10_000, state=State(state_file))
    by_id = {s.id: s for s in store.refresh()}
    ok, msg = store.trash(by_id["live"])
    assert not ok and "still open" in msg

    ok, msg = store.trash(by_id["gone"])
    assert ok, msg
    assert not path.exists() and not path.with_suffix("").exists()
    import sys as _sys
    trash = tmp_path / "home" / (".Trash" if _sys.platform == "darwin" else ".local/share/Trash/files")
    (folder,) = trash.iterdir()
    assert (folder / "gone.jsonl").exists() and (folder / "gone" / "subagents" / "agent-1.jsonl").exists()
    assert "move these back" in (folder / "WHERE THIS CAME FROM.txt").read_text()
    assert [s.id for s in store.refresh()] == ["live"]
