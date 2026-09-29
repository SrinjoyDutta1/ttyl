"""Two agents editing the same file at once."""

from datetime import datetime, timedelta, timezone

from ttyl import collide
from ttyl.demo import DemoStore
from ttyl.model import Session, Status, Turn
from ttyl.serve import Engine

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)


def mk(sid, agent, files, ago=10, status=Status.BUSY, cwd="/work/api", archived=False):
    s = Session(agent=agent, id=sid, cwd=cwd, status=status, archived=archived)
    t = Turn(started=NOW - timedelta(minutes=ago), prompt="x", done=True, ended=NOW - timedelta(minutes=ago))
    for f in files:
        t.add_file(f)
    s.turns.append(t)
    return s


def test_same_file_across_agents_collides():
    claude = mk("c", "claude", ["/work/api/src/limits.py", "/work/api/README.md"])
    codex = mk("x", "codex", ["src/limits.py"])  # Codex patches use repo-relative paths
    (c,) = collide.annotate([claude, codex], NOW)
    assert c.path == "/work/api/src/limits.py" and {s.id for s in c.sessions} == {"c", "x"}
    assert claude.collisions[0][1] == [codex] and codex.collisions[0][1] == [claude]


def test_not_a_collision():
    old = mk("old", "claude", ["/work/api/a.py"], ago=120)  # outside the window
    new = mk("new", "codex", ["/work/api/a.py"])
    both_closed = [mk("p", "claude", ["/work/api/b.py"], status=Status.CLOSED),
                   mk("q", "codex", ["/work/api/b.py"], status=Status.CLOSED)]
    memory = [mk("m1", "claude", ["/Users/me/.claude/projects/x/memory/MEMORY.md"]),
              mk("m2", "claude", ["/Users/me/.claude/projects/x/memory/MEMORY.md"])]
    hidden = [mk("h1", "claude", ["/work/api/c.py"]), mk("h2", "codex", ["/work/api/c.py"], archived=True)]
    assert collide.find([old, new, *both_closed, *memory, *hidden], NOW) == []


def test_demo_has_one_and_the_engine_alerts_only_on_new_ones():
    store = DemoStore()
    engine = Engine(store, ring=False, summaries=False)
    snap = engine.tick()
    assert snap["collisions"] == 1 and snap["alerts"] == []  # already happening at startup: quiet
    hit = [s for s in snap["sessions"] if s["collisions"]]
    assert len(hit) == 2 and hit[0]["collisions"][0]["path"] == "src/limits.py"

    engine._seen_collisions = set()  # pretend it just started
    (alert,) = engine.tick()["alerts"]
    assert "both editing src/limits.py" in alert["text"] and alert["title"].startswith("⚠ collision")
    assert engine.tick()["alerts"] == []
