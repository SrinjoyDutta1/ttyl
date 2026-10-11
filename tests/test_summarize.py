"""Summaries, with a fake Anthropic client (no network)."""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ttyl.claude import ClaudeParser
from ttyl.model import Commit, Session, Status, Turn
from ttyl.state import State
from ttyl.summarize import Summarizer, digest

from helpers import Claude


def session(builder, status=Status.IDLE, **kw) -> Session:
    s = Session(agent="claude", id="s1", cwd="/work/repo", status=status, **kw)
    p = ClaudeParser(s)
    for e in builder.events:
        p.feed(e)
    return s


class FakeMessages:
    def __init__(self, reply="**Doing** X. Next: Y.", stop="end_turn", error=None):
        self.reply, self.stop, self.error, self.calls = reply, stop, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="text", text=self.reply)])


def client(messages):
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def test_digest_has_what_a_summary_needs():
    b = (Claude().prompt("add rate limits").tool("Edit", {"file_path": "/work/repo/limits.py"}).say("Added them.")
         .prompt("push it").pending_tool("Bash", {"command": "git push"}))
    s = session(b, status=Status.WAITING, waiting_for="permission prompt")
    s.visible_turns[0].commits = [Commit("abc1234", "limits: token buckets", datetime.now(timezone.utc))]
    d = digest(s)
    assert "Developer: add rate limits" in d and "edited limits.py" in d
    assert 'committed abc1234 "limits: token buckets"' in d and "Agent said: Added them." in d
    assert "waiting for approval to: $ git push" in d and "blocked" in d


def test_summarize_saves_and_only_redoes_when_there_is_more(tmp_path):
    msgs = FakeMessages()
    state = State(tmp_path / "state.json")
    sm = Summarizer(state, client=client(msgs))
    s = session(Claude().prompt("do X").say("did X"))
    assert sm.needs(s)
    assert sm.summarize(s) == "Doing X. Next: Y."
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["output_config"] == {"effort": "low"}
    assert state.get("s1")["summary"]["turns"] == 1
    assert not sm.needs(s)

    s.turns.append(Turn(started=s.turns[0].started, prompt="now Y", done=True))  # a new turn arrives
    assert sm.needs(s)
    s.status = Status.BUSY
    assert not sm.needs(s)  # never mid-turn


def test_pick_prefers_the_selected_session():
    now = datetime.now(timezone.utc)
    a = session(Claude().prompt("a").say("a"))
    a.id = "a"
    b = session(Claude().prompt("b").say("b"))
    b.id = "b"
    sm = Summarizer(None, client=client(FakeMessages()))
    assert sm.pick([a, b], selected="b", cutoff=now - timedelta(days=3)) is b
    assert sm.pick([a, b], selected=None, cutoff=now - timedelta(days=3)) is a


def test_no_credentials_turns_summaries_off():
    msgs = FakeMessages(error=TypeError("Could not resolve authentication method. Expected one of api_key"))
    sm = Summarizer(None, client=client(msgs))
    s = session(Claude().prompt("x").say("y"))
    assert sm.summarize(s) is None
    assert not sm.enabled and "ANTHROPIC_API_KEY" in sm.disabled_reason
    assert sm.pick([s], None, datetime.now(timezone.utc)) is None


def test_refusal_is_not_retried_until_the_session_changes():
    sm = Summarizer(None, client=client(FakeMessages(stop="refusal")))
    s = session(Claude().prompt("x").say("y"))
    assert sm.summarize(s) is None and not s.summary
    assert not sm.needs(s)


def test_summaries_are_off_until_you_turn_them_on(monkeypatch):
    from ttyl import settings
    from ttyl.summarize import summaries_on

    assert not summaries_on()  # default: nothing is sent anywhere
    settings.set("summaries", True)
    assert summaries_on()
    monkeypatch.setenv("TTYL_SUMMARIES", "0")
    assert not summaries_on()


def test_the_engine_follows_the_setting(monkeypatch):
    from ttyl import summarize
    from ttyl.serve import Engine

    monkeypatch.setattr(summarize, "has_credentials", lambda: True)

    class Store:
        show_all = False
        days = 3

        def refresh(self):
            return []

    engine = Engine(Store(), ring=False)
    snap = engine.tick()
    assert snap["summaries"]["mode"] == "local" and snap["summaries"]["using"] == ""  # no helper in tests
    assert ("menu bar app" if os.uname().sysname == "Darwin" else "need a Mac") in snap["summaries"]["note"]
    engine.handle({"cmd": "summaries", "mode": "claude"})
    snap = engine.tick()
    assert snap["summaries"] == {"mode": "claude", "using": "claude", "on": True,
                                 "note": snap["summaries"]["note"]} and "billed" in snap["summaries"]["note"]
    assert "go to Claude" in snap["notices"][0]
    engine.handle({"cmd": "summaries", "mode": "off"})
    snap = engine.tick()
    assert snap["summaries"]["mode"] == "off" and engine.summarizer is None


FAKE_HELPER = """#!{python}
import json, sys
if "--check" in sys.argv:
    print(json.dumps({check!r}))
    sys.exit()
req = json.load(sys.stdin)
with open({log!r}, "a") as fh:
    fh.write(json.dumps(req) + "\\n")
print(json.dumps({{"error": "too long", "kind": "context"}} if len(req["prompt"]) > {limit} else {{"text": {reply!r}}}))
"""


def fake_helper(tmp_path, monkeypatch, reply="You're doing X. It did X.", available=True, limit=10**9):
    import sys

    log = tmp_path / "requests.jsonl"
    check = {"available": True} if available else {"available": False, "reason": "Apple Intelligence is off"}
    path = tmp_path / "ttyl-summarize"
    path.write_text(FAKE_HELPER.format(python=sys.executable, check=check, log=str(log),
                                       limit=limit, reply=reply))
    path.chmod(0o755)
    monkeypatch.setenv("TTYL_SUMMARIZE_HELPER", str(path))
    return log


def test_summaries_default_to_this_mac_and_off_means_off(monkeypatch):
    from ttyl.summarize import mode, set_mode

    assert mode() == "local"  # nothing leaves the Mac, so it's on out of the box
    set_mode("claude")
    assert mode() == "claude"
    set_mode("off")
    assert mode() == "off"
    set_mode("local")
    monkeypatch.setenv("TTYL_SUMMARIES", "0")
    assert mode() == "off"


def test_on_device_summary(tmp_path, monkeypatch):
    import json

    from ttyl.summarize import ON_DEVICE, choose

    log = fake_helper(tmp_path, monkeypatch, reply="Here's a summary:\n- You're fixing **X**.\n- It found Y.")
    state = State(tmp_path / "state.json")
    sm = choose(None, state)
    assert sm is not None and sm.kind == "local" and sm.model == ON_DEVICE
    s = session(Claude().prompt("fix X").say("found Y"), status=Status.WAITING, waiting_for="permission prompt")
    assert sm.summarize(s) == "You're fixing X. It found Y."  # opener, bullets and markdown gone
    assert state.get("s1")["summary"]["model"] == ON_DEVICE
    (req,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert "Developer: fix X" in req["prompt"] and "Status:" not in req["prompt"]  # ttyl says where it stands
    assert "two plain sentences" in req["instructions"]
    assert choose(sm, state) is sm  # reused while it works


def test_on_device_shrinks_a_long_log_to_fit(tmp_path, monkeypatch):
    import json

    from ttyl.summarize import choose

    log = fake_helper(tmp_path, monkeypatch, limit=400)
    b = Claude()
    for i in range(10):
        b = b.prompt(f"step {i}: " + "do the thing " * 5).say("done " * 10)
    sm = choose(None, None)
    assert sm.summarize(session(b)) == "You're doing X. It did X."
    sizes = [len(json.loads(line)["prompt"]) for line in log.read_text().splitlines()]
    assert sizes[-1] <= 400 < sizes[0]  # retried with fewer turns until it fit


def test_claude_without_a_key_falls_back_to_this_mac(tmp_path, monkeypatch):
    from ttyl import summarize
    from ttyl.summarize import choose, describe, set_mode

    fake_helper(tmp_path, monkeypatch)
    monkeypatch.setattr(summarize, "has_credentials", lambda: False)
    set_mode("claude")
    sm = choose(None, None)
    assert sm.kind == "local"
    assert describe(sm) == "Claude: no ANTHROPIC_API_KEY, so they're written on this Mac for now"
    monkeypatch.setattr(summarize, "has_credentials", lambda: True)
    assert choose(sm, None).kind == "claude"  # a key turned up


def test_rejected_key_pauses_claude_instead_of_retrying_every_refresh(tmp_path, monkeypatch):
    from ttyl import summarize
    from ttyl.summarize import Summarizer, choose, set_mode

    fake_helper(tmp_path, monkeypatch)
    monkeypatch.setattr(summarize, "has_credentials", lambda: True)
    set_mode("claude")
    claude = Summarizer(None, client=client(FakeMessages(error=TypeError("Could not resolve authentication"))))
    assert claude.summarize(session(Claude().prompt("x").say("y"))) is None
    assert choose(claude, None).kind == "local"
    assert choose(choose(claude, None), None).kind == "local"  # and it stays there


def test_no_apple_intelligence_means_no_summarizer_and_says_why(tmp_path, monkeypatch):
    from ttyl.summarize import choose, describe

    fake_helper(tmp_path, monkeypatch, available=False)
    assert choose(None, None) is None
    assert describe(None) == "Apple Intelligence is off"
