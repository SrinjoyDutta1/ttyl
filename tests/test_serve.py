"""The JSON engine behind the menu bar app, and its contract with the Swift side."""

import io
import json
import re
from pathlib import Path

from ttyl.demo import DemoStore
from ttyl.serve import Engine, serve, session_json, snapshot

MODEL_SWIFT = Path(__file__).resolve().parent.parent / "gui" / "Sources" / "Model.swift"


def test_snapshot_groups_numbers_and_rings():
    engine = Engine(DemoStore(), ring=False, summaries=False)
    snap = engine.tick()
    assert [s["key"] for s in snap["sections"]] == ["needs", "working", "finished", "idle", "closed"]
    first = snap["sessions"][0]
    assert first["number"] == 1 and first["ringing"] == "needs you" and first["action"].startswith("approve")
    assert snap["ringing"] == 2 and snap["needs_you"] == 1
    assert all(t["kind"] in {"edit", "run", "chat", "commit", "fail", "active", "waiting"}
               for s in snap["sessions"] for t in s["turns"])
    closed = [s for s in snap["sessions"] if s["section"] == "closed"]
    assert closed and all(" resume" in s["resume_command"] or "--resume" in s["resume_command"] for s in closed)


def test_go_stops_the_ring_and_says_what_it_did():
    engine = Engine(DemoStore(), ring=False, summaries=False)
    snap = engine.tick()
    target = snap["sessions"][0]["id"]
    engine.handle({"cmd": "go", "id": target})
    snap = engine.tick()
    assert snap["sessions"][0]["ringing"] == "" and snap["ringing"] == 1
    assert snap["notices"] == ["demo: would bring ttys004 to the front"]


def test_serve_streams_until_stdin_closes():
    store = DemoStore()
    first_id = snapshot(store.refresh())["sessions"][0]["id"]
    out = io.StringIO()
    inp = io.StringIO(json.dumps({"cmd": "go", "id": first_id}) + "\nnot json\n")
    assert serve(store, interval=0.05, inp=inp, out=out, ring=False, summaries=False) == 0
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(lines) == 2 and lines[-1]["notices"] == ["demo: would bring ttys004 to the front"]


def _swift_fields(struct: str) -> set[str]:
    src = MODEL_SWIFT.read_text()
    body = re.search(rf"struct {struct}\b[^{{]*{{(.*?)\n}}", src, re.S).group(1)
    return {m.strip("`") for m in re.findall(r"^    let (`?\w+`?):", body, re.M)}  # stored properties only


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(w.title() for w in rest)


def test_json_matches_the_swift_model():
    store = DemoStore()
    snap = snapshot(store.refresh())
    assert {_camel(k) for k in snap if k != "type"} == _swift_fields("Snapshot")
    assert {_camel(k) for k in snap["sections"][0]} == _swift_fields("SectionInfo")
    assert {_camel(k) for k in snap["sessions"][0]} == _swift_fields("SessionInfo")
    assert {_camel(k) for k in snap["sessions"][0]["turns"][0]} == _swift_fields("TurnInfo")
