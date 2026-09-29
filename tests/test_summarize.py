"""Summaries, with a fake Anthropic client (no network)."""

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
