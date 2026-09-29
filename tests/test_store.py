import json
import os
import subprocess
from datetime import timedelta

import pytest

from agentterm import procs as proclib
from agentterm.model import Kind, Status
from agentterm.store import Paths, Store, find

from helpers import T0, Claude


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
    return paths, procs


def register(paths, pid, sid, status="idle", kind="interactive", **extra):
    d = {"pid": pid, "sessionId": sid, "cwd": "/work/repo", "status": status, "kind": kind, "updatedAt": 1, **extra}
    (paths.claude_registry / f"{pid}.json").write_text(json.dumps(d))


def test_live_registry_joins_transcripts(home):
    paths, procs = home
    proj = paths.claude_projects / "-work-repo"
    Claude().prompt("deploy it").pending_tool("WebFetch", {"url": "https://example.com/pricing"}).write(proj / "aaa.jsonl")
    Claude().prompt("old work").say("done").write(proj / "bbb.jsonl")
    Claude().prompt("dead process").say("done").write(proj / "ccc.jsonl")

    procs[101] = proclib.Proc(101, 1, "ttys005", "claude")
    procs[102] = proclib.Proc(102, 1, "ttys007", "claude --resume bbb")
    procs[104] = proclib.Proc(104, 1, "ttys009", "claude")
    register(paths, 101, "aaa", status="waiting", waitingFor="permission prompt")
    register(paths, 102, "bbb", status="idle")
    register(paths, 103, "ccc", status="busy")  # pid 103 is gone: stale file
    register(paths, 104, "fresh", status="idle")  # opened, nothing typed yet
    register(paths, 105, "spare", kind="bg")
    procs[105] = proclib.Proc(105, 1, "", "claude bg-spare")

    st = Store(paths, days=10_000)
    sessions = st.refresh()
    by_id = {s.id: s for s in sessions}
    assert [s.id for s in sessions][:1] == ["aaa"]  # the one that needs you sorts first
    assert by_id["aaa"].status == Status.WAITING and by_id["aaa"].tty == "ttys005"
    assert by_id["aaa"].last_turn.kind == Kind.ACTIVE
    assert by_id["bbb"].status == Status.IDLE and by_id["bbb"].pid == 102
    assert by_id["ccc"].status == Status.CLOSED and by_id["ccc"].tty == ""
    assert by_id["fresh"].visible_turns == [] and by_id["fresh"].tty == "ttys009"
    assert "spare" not in by_id

    assert find(sessions, "5") is by_id["aaa"]
    assert find(sessions, "ttys007") is by_id["bbb"]
    assert find(sessions, "fres") is by_id["fresh"]  # id prefix, 4+ chars
    assert find(sessions, "bbb") is None  # too short to be an id prefix
    assert find(sessions, "ttys009") is by_id["fresh"]


def test_pid_reuse_is_not_a_live_agent(home):
    paths, procs = home
    Claude().prompt("x").say("y").write(paths.claude_projects / "-w" / "aaa.jsonl")
    register(paths, 200, "aaa", status="busy")
    procs[200] = proclib.Proc(200, 1, "ttys001", "/usr/bin/vim notes.txt")
    (s,) = Store(paths, days=10_000).refresh()
    assert s.status == Status.CLOSED


def test_incremental_tail(home):
    paths, _ = home
    path = paths.claude_projects / "-w" / "aaa.jsonl"
    b = Claude().prompt("one").say("1")
    b.write(path)
    st = Store(paths, days=10_000)
    assert len(st.refresh()[0].visible_turns) == 1

    with open(path, "a") as fh:  # agent appends a turn, the last line half-written
        b2 = Claude().prompt("two").say("2")
        lines = [json.dumps(e) for e in b2.events]
        fh.write(lines[0] + "\n" + lines[1][:10])
    assert [t.prompt for t in st.refresh()[0].visible_turns] == ["one", "two"]
    with open(path, "a") as fh:
        fh.write(lines[1][10:] + "\n")
    s = st.refresh()[0]
    assert s.last_turn.reply == "2"

    b.write(path)  # file rewritten shorter: start over
    assert [t.prompt for t in st.refresh()[0].visible_turns] == ["one"]


def test_recent_window(home):
    paths, _ = home
    old = Claude().prompt("ancient").say("x").write(paths.claude_projects / "-w" / "old.jsonl")
    os.utime(old, (1, 1))
    Claude().prompt("recent").say("x").write(paths.claude_projects / "-w" / "new.jsonl")
    st = Store(paths, days=3)
    assert [s.id for s in st.refresh()] == ["new"]
    st.show_all = True
    assert {s.id for s in st.refresh()} == {"new", "old"}


def test_commits_come_from_git_log(home, tmp_path):
    paths, _ = home
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args, when=None):
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        if when:
            env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = when.isoformat()
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)

    git("init", "-q")
    git("commit", "--allow-empty", "-m", "before the session", when=T0 - timedelta(hours=1))
    # Claude() events are one minute apart from T0: prompt at +1m, tool call +2m/+3m, reply +4m
    git("commit", "--allow-empty", "-m", "made by the agent", when=T0 + timedelta(minutes=2, seconds=30))
    git("commit", "--allow-empty", "-m", "made later by hand", when=T0 + timedelta(hours=2))

    b = (Claude(cwd=str(repo)).prompt("commit please")
         .tool("Bash", {"command": f"cd {repo} && git commit -m 'made by the agent'"}).say("done"))
    b.write(paths.claude_projects / "-repo" / "aaa.jsonl")
    (s,) = Store(paths, days=10_000).refresh()
    assert [c.subject for c in s.commits] == ["made by the agent"]
    assert s.last_turn.kind == Kind.COMMIT
