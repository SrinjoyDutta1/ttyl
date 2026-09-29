"""OpenCode, GitHub Copilot CLI, Goose and Aider, from records shaped like their sources write them."""

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from ttyl import procs as proclib
from ttyl.model import Kind, Status
from ttyl.store import Paths, Store
from ttyl.terminal import resume_command


def iso(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def ms(minutes_ago=0):
    return int((time.time() - minutes_ago * 60) * 1000)


@pytest.fixture
def home(tmp_path, monkeypatch):
    paths = Paths(claude_projects=tmp_path / "c" / "projects", claude_registry=tmp_path / "c" / "sessions",
                  codex_sessions=tmp_path / "x" / "sessions", codex_index=tmp_path / "x" / "i.jsonl",
                  codex_home=tmp_path / "x", gemini_home=tmp_path / "g", qwen_home=tmp_path / "q",
                  copilot_home=tmp_path / "copilot", opencode_data=tmp_path / "opencode",
                  goose_data=tmp_path / "goose")
    paths.claude_registry.mkdir(parents=True)
    procs, cwds = {}, {}
    monkeypatch.setattr(proclib, "snapshot", lambda: procs)
    monkeypatch.setattr(proclib, "cwd_of", lambda pid: cwds.get(pid))
    return paths, procs, cwds, tmp_path


def test_opencode(home):
    paths, procs, cwds, _ = home
    paths.opencode_data.mkdir(parents=True)
    db = sqlite3.connect(paths.opencode_data / "opencode.db")
    db.executescript("""
        create table session (id text primary key, project_id text, parent_id text, directory text, title text,
                              version text, time_created integer, time_updated integer, time_archived integer);
        create table message (id text primary key, session_id text, time_created integer, time_updated integer, data text);
        create table part (id text primary key, message_id text, session_id text, time_created integer, data text);
    """)
    db.execute("insert into session values ('ses_1', 'p', null, '/work/web', 'New session - 2026-09-29T14:00:00.000Z', '1.18', ?, ?, null)", (ms(20), ms(1)))
    db.execute("insert into session values ('ses_child', 'p', 'ses_1', '/work/web', 'subagent', '1.18', ?, ?, null)", (ms(20), ms(1)))
    rows = [
        ("msg_1", {"role": "user", "time": {"created": ms(20)}}, [{"type": "text", "text": "make the header sticky"}]),
        ("msg_2", {"role": "assistant", "time": {"created": ms(19), "completed": ms(15)}}, [
            {"type": "text", "text": "Made it sticky and committed."},
            {"type": "tool", "callID": "a", "tool": "edit", "state": {"status": "completed", "input": {"filePath": "/work/web/src/Header.tsx"}}},
            {"type": "patch", "hash": "h", "files": ["/work/web/src/header.css"]},
            {"type": "tool", "callID": "b", "tool": "bash", "state": {"status": "completed", "input": {"command": "git commit -am sticky"}}}]),
        ("msg_3", {"role": "user", "time": {"created": ms(3)}}, [{"type": "text", "text": "now run the tests"}]),
        ("msg_4", {"role": "assistant", "time": {"created": ms(2)}}, [  # no time.completed: still working
            {"type": "tool", "callID": "c", "tool": "bash", "state": {"status": "running", "input": {"command": "npm test"}}}]),
    ]
    for i, (mid, data, parts) in enumerate(rows):
        db.execute("insert into message values (?, 'ses_1', ?, ?, ?)", (mid, data["time"]["created"], data["time"]["created"], json.dumps(data)))
        for j, part in enumerate(parts):
            db.execute("insert into part values (?, ?, 'ses_1', 0, ?)", (f"prt_{i}{j}", mid, json.dumps(part)))
    db.commit()
    procs[60] = proclib.Proc(60, 1, "ttys011", "/Users/me/.opencode/bin/opencode")
    cwds[60] = "/work/web"

    (s,) = Store(paths, days=3).refresh()
    first, second = s.visible_turns
    assert s.title == "make the header sticky"  # the placeholder title is ignored
    assert first.files == ["/work/web/src/Header.tsx", "/work/web/src/header.css"] and first.kind == Kind.COMMIT
    assert second.kind == Kind.ACTIVE and second.last_action == "$ npm test"
    assert s.status == Status.BUSY and s.tty == "ttys011"
    assert resume_command(s) == "cd /work/web && opencode --session ses_1"


def test_copilot(home):
    paths, procs, cwds, _ = home
    d = paths.copilot_home / "session-state" / "5f2c"
    d.mkdir(parents=True)
    (d / "workspace.yaml").write_text("id: 5f2c\ncwd: /work/cli\ngit_root: /work/cli\nbranch: fix/build\nname: Fix the build\n")
    events = [
        {"type": "session.start", "id": "e0", "timestamp": iso(10), "data": {"context": {"cwd": "/work/cli"}}},
        {"type": "user.message", "id": "e1", "timestamp": iso(10), "data": {"content": "make it build"}},
        {"type": "assistant.turn_start", "id": "e2", "timestamp": iso(10), "data": {}},
        {"type": "tool.execution_start", "id": "e3", "timestamp": iso(9), "data": {"toolCallId": "t1", "toolName": "edit", "arguments": {"path": "/work/cli/main.go"}}},
        {"type": "tool.execution_complete", "id": "e4", "timestamp": iso(9), "data": {"toolCallId": "t1", "success": True}},
        {"type": "assistant.message", "id": "e5", "timestamp": iso(8), "data": {"content": "Fixed the import; it builds."}},
        {"type": "assistant.turn_end", "id": "e6", "timestamp": iso(8), "data": {}},
    ]
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    procs[61] = proclib.Proc(61, 1, "ttys012", "copilot")
    cwds[61] = "/work/cli"

    (s,) = Store(paths, days=3).refresh()
    (t,) = s.visible_turns
    assert (s.id, s.title, s.branch) == ("5f2c", "Fix the build", "fix/build")
    assert t.files == ["/work/cli/main.go"] and t.done and t.reply == "Fixed the import; it builds."
    assert s.status == Status.IDLE
    assert resume_command(s) == "cd /work/cli && copilot --resume=5f2c"


def test_goose(home):
    paths, _, _, _ = home
    (paths.goose_data / "sessions").mkdir(parents=True)
    db = sqlite3.connect(paths.goose_data / "sessions" / "sessions.db")
    db.executescript("""
        create table sessions (id text primary key, name text, description text, user_set_name boolean,
                               session_type text, working_dir text, created_at timestamp, updated_at timestamp);
        create table messages (id integer primary key autoincrement, message_id text, session_id text, role text,
                               content_json text, created_timestamp integer, timestamp timestamp);
    """)
    now = datetime.now(timezone.utc)
    stamp = lambda m: (now - timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("insert into sessions values ('20260929_3', 'Tidy the Makefile', '', 0, 'user', '/work/tools', ?, ?)", (stamp(30), stamp(5)))
    msgs = [
        ("user", [{"type": "text", "text": "tidy up the Makefile"}], 30),
        ("assistant", [{"type": "text", "text": "Looking."}, {"type": "toolRequest", "id": "r1", "toolCall": {"status": "success",
            "value": {"name": "developer__text_editor", "arguments": {"command": "str_replace", "path": "/work/tools/Makefile"}}}}], 29),
        ("user", [{"type": "toolResponse", "id": "r1", "toolResult": {"status": "success"}}], 29),
        ("assistant", [{"type": "text", "text": "Removed the dead targets."}], 28),
    ]
    for role, blocks, m in msgs:
        db.execute("insert into messages (session_id, role, content_json, created_timestamp) values ('20260929_3', ?, ?, ?)",
                   (role, json.dumps(blocks), int(time.time() - m * 60)))
    db.commit()

    (s,) = Store(paths, days=3).refresh()
    (t,) = s.visible_turns
    assert (s.title, s.cwd) == ("Tidy the Makefile", "/work/tools")
    assert t.files == ["/work/tools/Makefile"] and t.done and t.reply == "Removed the dead targets."
    assert s.status == Status.CLOSED  # no goose process
    assert resume_command(s) == "cd /work/tools && goose session --resume --session-id 20260929_3"


def test_aider(home):
    paths, procs, cwds, tmp = home
    repo = tmp / "repo"
    repo.mkdir()
    (repo / ".aider.chat.history.md").write_text("""
# aider chat started at 2026-09-20 10:00:00

#### an old run
Old answer.

# aider chat started at 2026-09-29 14:02:11

> /Users/me/.local/bin/aider --model sonnet
> Aider v0.86.2

#### /add cli.py

> Added cli.py to the chat

#### add a --verbose
#### flag to cli.py

I'll add it.

cli.py
<<<<<<< SEARCH
=======
>>>>>>> REPLACE

> Tokens: 5.2k sent, 180 received.
> Applied edit to cli.py
> Commit 3f9a2c1 feat: add --verbose flag

#### now write the tests
""")
    procs[62] = proclib.Proc(62, 1, "ttys013", "/usr/bin/python3 /Users/me/.local/bin/aider --model sonnet")
    cwds[62] = str(repo)

    (s,) = Store(paths, days=3).refresh()
    turns = s.visible_turns
    assert [t.prompt for t in turns] == ["add a --verbose flag to cli.py", "now write the tests"]  # /add answered by no one: hidden
    first, second = turns
    assert first.files == [str(repo / "cli.py")] and first.kind == Kind.COMMIT
    assert first.commits[0].sha == "3f9a2c1" and first.reply.startswith("I'll add it.")
    assert second.kind == Kind.ACTIVE and s.status == Status.BUSY and s.tty == "ttys013"
    assert resume_command(s) == f"cd {repo} && aider --restore-chat-history"
