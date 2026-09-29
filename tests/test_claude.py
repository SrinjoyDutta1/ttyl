from agentterm.claude import ClaudeParser
from agentterm.model import Kind, Session

from helpers import Claude


def parse(builder: Claude) -> Session:
    s = Session(agent="claude", id="s1")
    p = ClaudeParser(s)
    for e in builder.events:
        p.feed(e)
    return s


def test_turn_kinds():
    b = (Claude()
         .prompt("just a question").say("an answer")
         .prompt("look around").tool("Bash", {"command": "ls"}).tool("Read", {"file_path": "/work/repo/a.py"}).say("done")
         .prompt("fix it").tool("Edit", {"file_path": "/work/repo/a.py"}).say("fixed")
         .prompt("commit").tool("Bash", {"command": "cd /work/repo && git add -A && git commit -m x"}, stdout="[main abc1234] x").say("ok"))
    s = parse(b)
    assert [t.kind for t in s.visible_turns] == [Kind.CHAT, Kind.RUN, Kind.EDIT, Kind.COMMIT]
    assert s.visible_turns[2].files == ["/work/repo/a.py"]
    assert s.visible_turns[3].commit_dirs == ["/work/repo"]
    assert s.cwd == "/work/repo" and s.branch == "main"


def test_last_turn_active_until_end_turn():
    s = parse(Claude().prompt("run tests").pending_tool("Bash", {"command": "cd /x && pytest -q"}))
    t = s.last_turn
    assert t.kind == Kind.ACTIVE
    assert t.last_action == "$ pytest -q"


def test_interrupt_and_failed_commit():
    b = (Claude()
         .prompt("do a thing").pending_tool("Bash", {"command": "sleep 100"})
         .prompt("[Request interrupted by user for tool use]")
         .prompt("commit").tool("Bash", {"command": "git commit -m y"}, result="hook failed", is_error=True).say("the hook failed"))
    s = parse(b)
    assert [t.kind for t in s.visible_turns] == [Kind.FAIL, Kind.RUN]
    assert s.visible_turns[1].tool_errors == 1


def test_noise_is_not_a_turn():
    b = (Claude()
         .prompt("<command-name>/model</command-name>\n<command-message>model</command-message>\n<command-args></command-args>")
         .prompt("<local-command-stdout>Set model to opus</local-command-stdout>")
         .raw(type="user", message={"role": "user", "content": "skill body"}, isMeta=True)
         .raw(type="assistant", isSidechain=True, message={"role": "assistant", "content": [{"type": "text", "text": "sub"}], "stop_reason": "end_turn"})
         .prompt("real question").say("real answer"))
    s = parse(b)
    assert [t.prompt for t in s.visible_turns] == ["real question"]


def test_slash_command_that_the_agent_answers_is_kept():
    b = Claude().prompt("<command-name>/code-review</command-name><command-args>high</command-args>").say("reviewing")
    s = parse(b)
    assert [(t.prompt, t.origin) for t in s.visible_turns] == [("/code-review high", "command")]


def test_titles_recap_and_pastes():
    b = (Claude()
         .prompt('fix <pasted_content id="1">Traceback: boom</pasted_content> please').say("ok")
         .prompt('<pasted_content id="2">only a paste</pasted_content>').say("ok")
         .prompt('<pasted_content id="3">\nhttps://doc</pasted_content id="3"> i dont like it').say("ok")
         .raw(type="ai-title", aiTitle="Fix the boom")
         .raw(type="system", subtype="away_summary", content="Goal was X; did Y."))
    s = parse(b)
    assert s.title == "Fix the boom"
    assert s.visible_turns[0].prompt == "fix  [pasted]  please"
    assert s.visible_turns[1].prompt == "[pasted] only a paste"
    assert s.visible_turns[2].prompt == "[pasted]  i dont like it"
    assert s.away_summary == "Goal was X; did Y."
    s.custom_title = "mine"
    assert s.title == "mine"


def test_task_notification_is_a_system_turn():
    b = (Claude().prompt("start the job").say("started")
         .prompt("<task-notification><summary>Tests finished</summary></task-notification>").say("all green"))
    s = parse(b)
    assert s.visible_turns[1].origin == "system"
    assert s.visible_turns[1].prompt == "⚙ Tests finished"


def test_title_falls_back_to_first_prompt():
    s = parse(Claude().prompt("make the thing faster").say("ok"))
    assert s.title == "make the thing faster"
