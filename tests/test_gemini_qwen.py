"""Gemini CLI and Qwen Code, from records shaped like their source code writes them
(gemini-cli packages/core/src/services/chatRecording*.ts, qwen-code chatRecordingService.ts)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from ttyl import procs as proclib
from ttyl.gemini import GeminiParser
from ttyl.model import Kind, Session, Status
from ttyl.qwen import QwenParser
from ttyl.store import Paths, Store
from ttyl.terminal import resume_command


def iso(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def gemini_log():
    return [
        {"sessionId": "g-123", "projectHash": "abc", "startTime": iso(30), "lastUpdated": iso(30), "kind": "main"},
        {"id": "u1", "timestamp": iso(30), "type": "user", "content": [{"text": "fix the typo in README and commit"}]},
        {"$set": {"lastUpdated": iso(29)}},
        {"id": "g1", "timestamp": iso(29), "type": "gemini", "content": "", "model": "gemini-2.5-pro"},
        # the same message re-appended once its tool calls finished: the last copy wins
        {"id": "g1", "timestamp": iso(29), "type": "gemini", "content": "", "toolCalls": [
            {"id": "t1", "name": "replace", "args": {"file_path": "README.md", "old_string": "teh", "new_string": "the"},
             "status": "success", "timestamp": iso(29),
             "resultDisplay": {"fileName": "README.md", "filePath": "/work/site/README.md"}},
            {"id": "t2", "name": "run_shell_command", "args": {"command": "git commit -am 'fix typo'"},
             "status": "success", "timestamp": iso(28)}]},
        {"id": "u2", "timestamp": iso(28), "type": "user", "content": [{"functionResponse": {"name": "replace"}}]},
        {"id": "g2", "timestamp": iso(27), "type": "gemini", "content": "Fixed and committed."},
        {"id": "u3", "timestamp": iso(26), "type": "user", "content": [{"text": "/stats"}]},  # a slash command, not a prompt
        {"id": "u4", "timestamp": iso(5), "type": "user", "content": [{"text": "now translate it to French"}]},
    ]


def parse_gemini(records, cwd="/work/site"):
    s = Session(agent="gemini", id="file", cwd=cwd)
    p = GeminiParser(s)
    for r in records:
        p.feed(r)
    p.flush()
    return s, p


def test_gemini_turns_edits_commits_and_last_copy_wins():
    s, _ = parse_gemini(gemini_log())
    assert s.id == "g-123"
    first, second = s.visible_turns
    assert first.prompt == "fix the typo in README and commit" and first.reply == "Fixed and committed."
    assert first.files == ["/work/site/README.md"] and first.kind == Kind.COMMIT
    assert second.prompt == "now translate it to French" and second.kind == Kind.ACTIVE  # no answer yet
    assert s.title == "fix the typo in README and commit"


def test_gemini_summary_rewind_and_checkpoint():
    log = gemini_log() + [{"$set": {"summary": "Fix README typo"}}, {"$rewindTo": "u4"}]
    s, p = parse_gemini(log)
    assert s.title == "Fix README typo" and [t.prompt for t in s.visible_turns] == ["fix the typo in README and commit"]
    p.feed({"$set": {"messages": [{"id": "x", "timestamp": iso(1), "type": "user", "content": "start over"}]}})
    p.flush()
    assert [t.prompt for t in s.visible_turns] == ["start over"]


def test_gemini_subagent_logs_are_skipped():
    s, _ = parse_gemini([{"sessionId": "sub", "projectHash": "abc", "kind": "subagent"}])
    assert s.imported


@pytest.fixture
def home(tmp_path, monkeypatch):
    paths = Paths(claude_projects=tmp_path / "c" / "projects", claude_registry=tmp_path / "c" / "sessions",
                  codex_sessions=tmp_path / "x" / "sessions", codex_index=tmp_path / "x" / "i.jsonl",
                  codex_home=tmp_path / "x", gemini_home=tmp_path / "gemini", qwen_home=tmp_path / "qwen")
    paths.claude_registry.mkdir(parents=True)
    procs, cwds = {}, {}
    monkeypatch.setattr(proclib, "snapshot", lambda: procs)
    monkeypatch.setattr(proclib, "cwd_of", lambda pid: cwds.get(pid))
    return paths, procs, cwds


def test_gemini_in_the_store(home):
    paths, procs, cwds = home
    project = paths.gemini_home / "tmp" / "site"
    (project / "chats").mkdir(parents=True)
    (project / ".project_root").write_text("/work/site\n")
    f = project / "chats" / "session-2026-09-29T14-05-g-123abc.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in gemini_log()))
    legacy = project / "chats" / "session-2026-08-01T10-00-old.json"
    legacy.write_text(json.dumps({"sessionId": "old-1", "projectHash": "abc", "startTime": iso(20),
                                  "lastUpdated": iso(20), "messages": [
                                      {"id": "a", "timestamp": iso(20), "type": "user", "content": "hello from 0.38"},
                                      {"id": "b", "timestamp": iso(19), "type": "gemini", "content": "hi"}]}, indent=2))
    # gemini relaunches itself: a parent and a child on the same tab
    procs[70] = proclib.Proc(70, 1, "ttys009", "node /opt/homebrew/lib/node_modules/@google/gemini-cli/bundle/gemini.js")
    procs[71] = proclib.Proc(71, 70, "ttys009", "node --max-old-space-size=8192 /opt/homebrew/lib/node_modules/@google/gemini-cli/bundle/gemini.js")
    cwds[70] = cwds[71] = "/work/site"

    by_id = {s.id: s for s in Store(paths, days=3).refresh()}
    live, old = by_id["g-123"], by_id["old-1"]
    assert live.cwd == "/work/site" and live.tty == "ttys009" and live.status == Status.BUSY
    assert old.status == Status.CLOSED and old.visible_turns[0].prompt == "hello from 0.38"
    assert resume_command(old) == "cd /work/site && gemini --resume old-1"


def test_qwen(home):
    paths, procs, cwds = home
    base = {"sessionId": "q-9", "cwd": "/work/api", "version": "0.24.7", "gitBranch": "dev", "parentUuid": None}
    records = [
        {**base, "uuid": "1", "timestamp": iso(10), "type": "user", "message": {"role": "user", "parts": [{"text": "add a health check"}]}},
        {**base, "uuid": "2", "timestamp": iso(9), "type": "assistant", "message": {"role": "model", "parts": [
            {"text": "Adding it."}, {"functionCall": {"id": "c1", "name": "edit", "args": {"file_path": "/work/api/app.py"}}}]}},
        {**base, "uuid": "3", "timestamp": iso(9), "type": "tool_result", "toolCallResult": {"callId": "c1", "status": "success"},
         "message": {"role": "user", "parts": [{"functionResponse": {"name": "edit"}}]}},
        {**base, "uuid": "4", "timestamp": iso(8), "type": "assistant", "message": {"role": "model", "parts": [{"text": "Done: /health returns 200."}]}},
        {**base, "uuid": "5", "timestamp": iso(8), "type": "system", "subtype": "turn_result"},
        {**base, "uuid": "6", "timestamp": iso(8), "type": "system", "subtype": "custom_title", "systemPayload": {"customTitle": "Health check"}},
    ]
    s = Session(agent="qwen", id="f")
    p = QwenParser(s)
    for r in records:
        p.feed(r)
    (t,) = s.visible_turns
    assert (s.id, s.cwd, s.branch, s.title) == ("q-9", "/work/api", "dev", "Health check")
    assert t.files == ["/work/api/app.py"] and t.done and t.reply == "Done: /health returns 200."

    d = paths.qwen_home / "projects" / "-work-api" / "chats"
    d.mkdir(parents=True)
    (d / "q-9.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    procs[80] = proclib.Proc(80, 1, "ttys010", "qwen")
    cwds[80] = "/work/api"
    (s,) = Store(paths, days=3).refresh()
    assert s.status == Status.IDLE and s.tty == "ttys010"
    del procs[80]
    (s,) = Store(paths, days=3).refresh()
    assert resume_command(s) == "cd /work/api && qwen --resume q-9"
