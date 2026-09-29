import json

from ttyl.codex import CodexParser, load_titles
from ttyl.model import Kind, Session

from helpers import codex_line


def parse(lines) -> Session:
    s = Session(agent="codex", id="rollout")
    p = CodexParser(s)
    for d in lines:
        p.feed(d)
    return s


META = codex_line("session_meta", {"id": "abc", "cwd": "/work/repo", "originator": "codex_cli_rs", "git": {"branch": "dev"}}, 0)


def call(name, args, call_id, minute, custom=False):
    if custom:
        return codex_line("response_item", {"type": "custom_tool_call", "name": name, "input": args, "call_id": call_id}, minute)
    return codex_line("response_item", {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": call_id}, minute)


def out(call_id, text, minute, custom=False):
    kind = "custom_tool_call_output" if custom else "function_call_output"
    return codex_line("response_item", {"type": kind, "call_id": call_id, "output": text}, minute)


def test_cli_session():
    s = parse([
        META,
        codex_line("event_msg", {"type": "task_started", "turn_id": "t1"}, 1),
        codex_line("event_msg", {"type": "user_message", "message": "add a flag"}, 1),
        call("shell", {"command": ["bash", "-lc", "cd /work/repo && rg flag"]}, "c1", 2),
        out("c1", "Exit code: 0\nOutput:\nfound", 2),
        call("apply_patch", "*** Begin Patch\n*** Update File: src/cli.py\n@@\n+x\n*** End Patch", "c2", 3, custom=True),
        out("c2", "Success", 3, custom=True),
        codex_line("event_msg", {"type": "agent_message", "message": "Added --flag."}, 4),
        codex_line("event_msg", {"type": "task_complete", "turn_id": "t1", "last_agent_message": "Added --flag."}, 4),
        codex_line("event_msg", {"type": "user_message", "message": "commit it"}, 5),
        call("exec_command", {"cmd": "git commit -am 'add flag'"}, "c3", 6),
        out("c3", "Process exited with code 0\nOutput:\n[dev 1234abc] add flag", 6),
        codex_line("event_msg", {"type": "task_complete", "turn_id": "t2"}, 7),
        codex_line("event_msg", {"type": "user_message", "message": "run tests"}, 8),
        call("exec_command", {"cmd": "pytest"}, "c4", 9),
        out("c4", "Process exited with code 1\nOutput:\nFAILED", 9),
    ])
    assert (s.id, s.cwd, s.branch, s.entrypoint) == ("abc", "/work/repo", "dev", "cli")
    t1, t2, t3 = s.visible_turns
    assert t1.kind == Kind.EDIT and t1.files == ["src/cli.py"] and t1.reply == "Added --flag."
    assert t2.kind == Kind.COMMIT and t2.commit_dirs == ["/work/repo"]
    assert t3.kind == Kind.ACTIVE and t3.tool_errors == 1 and t3.last_action == "$ pytest"


def test_desktop_js_exec_and_abort():
    js = 'const r = await tools.exec_command({"cmd":"sed -n \\"1,5p\\" README.md","workdir":"/w"});'
    s = parse([
        codex_line("session_meta", {"id": "d1", "cwd": "/w", "originator": "Codex Desktop"}, 0),
        codex_line("event_msg", {"type": "user_message", "message": "read the readme"}, 1),
        call("exec", js, "j1", 2, custom=True),
        out("j1", [{"type": "input_text", "text": "Script completed"}], 2, custom=True),
        codex_line("event_msg", {"type": "turn_aborted", "reason": "interrupted"}, 3),
    ])
    assert s.entrypoint == "desktop"
    t = s.last_turn
    assert t.commands == ['sed -n "1,5p" README.md']
    assert t.kind == Kind.FAIL


def test_imported_sessions_are_flagged():
    s = parse([META, codex_line("event_msg", {"type": "task_started", "turn_id": "external-import-turn-1"}, 1)])
    assert s.imported


def test_thread_names(tmp_path):
    idx = tmp_path / "session_index.jsonl"
    idx.write_text('{"id":"a","thread_name":"old"}\n{"id":"a","thread_name":"new"}\nnot json\n')
    assert load_titles(idx) == {"a": "new"}
