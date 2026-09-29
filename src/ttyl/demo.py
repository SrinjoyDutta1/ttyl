"""`ttyl --demo`: a made-up afternoon of agent sessions.

For screenshots and for trying the UI without touching real transcripts.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from . import collide
from .model import Commit, Session, Status, Turn
from .store import _sort_key

HOME = "/Users/you/code"


def _session(agent, project, branch, title, status, tty, turns, *, updated, reply="", recap="", recap_ago=None,
             waiting_for="", pid=None, summary="", closed=None, args=()) -> Session:
    now = datetime.now(timezone.utc)
    s = Session(agent=agent, id=f"demo-{project}-{title[:12]}".replace(" ", "-").lower(), cwd=f"{HOME}/{project}",
                branch=branch, ai_title=title, status=status, tty=tty, pid=pid, waiting_for=waiting_for,
                entrypoint="cli", updated=now - timedelta(minutes=updated))
    start = updated + 12 * len(turns)
    for i, spec in enumerate(turns):
        t = _turn(s, now - timedelta(minutes=start - 12 * i), *spec)
        s.turns.append(t)
    s.started = s.turns[0].started if s.turns else s.updated
    if reply and s.turns:
        s.turns[-1].reply = reply
    if recap:
        s.away_summary = recap
        s.away_at = now - timedelta(minutes=recap_ago if recap_ago is not None else updated)
    if summary:
        s.summary, s.summary_turns, s.summary_at = summary, len(s.visible_turns), now - timedelta(minutes=updated)
    if closed is not None:
        s.closed_at = now - timedelta(minutes=closed)
    s.launch_args = list(args)
    return s


def _turn(s: Session, when: datetime, prompt: str, kind: str, *extra) -> Turn:
    t = Turn(started=when, prompt=prompt, done=True, had_agent=True, ended=when + timedelta(minutes=4))
    if kind in ("edit", "commit"):
        for f in extra[0] if extra else ():
            t.add_file(f"{s.cwd}/{f}")
        t.tools = Counter({"Edit": len(t.files) or 1, "Bash": 1})
    if kind == "run":
        t.tools = Counter({"Bash": 2, "Read": 1})
    if kind == "commit":
        sha, subject = extra[1]
        t.committed = True
        t.commits = [Commit(sha, subject, when + timedelta(minutes=3))]
    if kind == "fail":
        t.tools = Counter({"Bash": 1})
        t.interrupted = True
    if kind == "active":
        t.done = False
        t.tools = Counter({"Bash": 1})
        t.last_action = extra[0]
    return t


def sessions() -> list[Session]:
    out = [
        _session("claude", "api-gateway", "feat/rate-limits", "Per-tenant rate limiting", Status.WAITING, "ttys004", [
            ("add per-tenant rate limits to the gateway, redis-backed", "edit", ["src/limits.py", "src/middleware.py"]),
            ("what happens when redis is down?", "chat"),
            ("fail open, but log it loudly", "edit", ["src/limits.py"]),
            ("run the load test against staging", "run"),
            ("commit this", "commit", ["src/limits.py"], ("a3f9c21", "limits: per-tenant token buckets in redis")),
            ("add tests for the fail-open path", "edit", ["tests/test_limits.py"]),
            ("ok push it and open a PR", "active", "$ git push -u origin feat/rate-limits"),
        ], summary="Adding per-tenant rate limiting to the gateway, backed by Redis. It built the token buckets, made them fail open when Redis is down, load-tested them, committed a3f9c21 and added tests for the fail-open path. It is waiting for you to approve pushing feat/rate-limits so it can open the PR.", updated=3, waiting_for="permission prompt", pid=41822,
            recap="Per-tenant rate limiting is built and committed, and the fail-open path has tests. "
                  "Next: push feat/rate-limits and open the PR, waiting on your OK to push."),
        _session("claude", "web-dashboard", "main", "Fix the flaky checkout e2e test", Status.BUSY, "ttys002", [
            ("the checkout e2e test fails about 1 in 5 runs, find out why", "run"),
            ("[pasted] Error: Timed out waiting for selector [data-test=pay]", "run"),
            ("so it's a race with the price fetch?", "chat"),
            ("fix it properly, no sleeps", "edit", ["e2e/checkout.spec.ts", "src/Checkout.tsx"]),
            ("run it 50 times", "active", "$ npx playwright test checkout --repeat-each 50"),
        ], summary="Tracking down why the checkout e2e test fails about one run in five. The cause was a race: the Pay button rendered before the price request resolved, so it now waits on the price query. It is running the test 50 times to prove the flake is gone.", updated=0, pid=41907, reply="Found it: the Pay button renders before the price request resolves. "
                                       "I made the button wait on the price query instead of a timeout."),
        _session("codex", "api-gateway", "main", "Add structured request logging", Status.BUSY, "ttys008", [
            ("log every request as JSON, include the tenant id", "edit", ["src/middleware.py", "src/limits.py"]),
            ("run the logging tests", "active", "$ pytest tests/test_logging.py -q"),
        ], updated=0, pid=42377,
            summary="Adding JSON request logging to the gateway, with the tenant id on every line. It changed "
                    "middleware.py and limits.py and is running the logging tests."),
        _session("codex", "ml-pipeline", "exp/lora", "Sweep LoRA ranks on the eval set", Status.BUSY, "ttys007", [
            ("set up a sweep over lora ranks 4, 8, 16, 32", "edit", ["sweep.py", "configs/lora.yaml"]),
            ("use the held-out eval split, not dev", "edit", ["configs/lora.yaml"]),
            ("launch it", "fail"),
            ("launch it, 2 GPUs max", "active", "$ python sweep.py --ranks 4 8 16 32 --gpus 2"),
        ], updated=1, pid=42210, reply="Config points at eval-heldout now. Launching with a 2-GPU cap."),
        _session("claude", "mobile-app", "main", "Dark mode for the settings screen", Status.IDLE, "ttys003", [
            ("add dark mode to the settings screen", "edit", ["app/Settings.tsx", "app/theme.ts"]),
            ("the toggle doesn't persist after a restart", "edit", ["app/Settings.tsx", "app/storage.ts"]),
            ("commit", "commit", ["app/Settings.tsx"], ("7c1e0b4", "settings: dark mode toggle, persisted")),
            ("add a snapshot test for both themes", "edit", ["app/__tests__/Settings.test.tsx"]),
            ("commit that too", "commit", ["app/__tests__/Settings.test.tsx"], ("91d2f3a", "tests: snapshot settings in both themes")),
        ], summary="Dark mode for the settings screen is done: the toggle persists across restarts (7c1e0b4) and a snapshot test covers both themes (91d2f3a). Both commits are on main; nothing is left unless you want dark mode elsewhere.", updated=25, pid=40115, reply="Both commits are on main. The snapshot test covers light and dark and passes."),
        _session("claude", "infra", "main", "Move the logs bucket to us-east-2", Status.IDLE, "ttys001", [
            ("plan moving the logs bucket to us-east-2", "run"),
            ("why does it want to destroy the bucket?", "chat"),
            ("use a moved block instead", "edit", ["logs.tf"]),
            ("plan again", "run"),
        ], summary="Moving the logs bucket to us-east-2 without destroying it. It replaced the destroy-and-recreate plan with a moved block in logs.tf, and the plan is now clean (0 to destroy). Next step is yours: terraform apply -target=module.logs.", updated=60 * 72, pid=38801, reply="The plan is clean now: 0 to destroy, 1 to change.\n"
                                            "Apply it when you're ready: terraform apply -target=module.logs"),
        _session("gemini", "docs-site", "main", "Rewrite the quickstart", Status.IDLE, "ttys006", [
            ("the quickstart is too long, what would you cut?", "chat"),
            ("do it", "edit", ["docs/quickstart.md"]),
            ("add a copy button to code blocks", "edit", ["src/components/Code.tsx"]),
            ("does it build?", "run"),
        ], updated=60 * 24 * 5, pid=37230, reply="Builds clean. The quickstart is down from 9 steps to 4."),
        _session("claude", "api-gateway", "main", "Investigate the p99 latency spike", Status.CLOSED, "", [
            ("p99 on /v1/requests jumped at 14:00, why?", "run"),
            ("[pasted] EXPLAIN ANALYZE SELECT ...", "run"),
            ("so it's a seq scan on requests?", "chat"),
            ("add the index", "edit", ["migrations/0042_requests_tenant_idx.sql"]),
            ("commit", "commit", ["migrations/0042_requests_tenant_idx.sql"], ("4be2a77", "db: index requests(tenant_id, created_at)")),
        ], summary="Investigated the 14:00 p99 spike on /v1/requests: a sequential scan on the requests table. It added an index on (tenant_id, created_at) and committed the migration (4be2a77); p99 should drop to about 80ms once it runs.", closed=95, args=("--model", "opus"), updated=130, reply="Committed. p99 should drop back to about 80ms once the migration runs."),
        _session("codex", "dotfiles", "main", "Speed up zsh startup", Status.CLOSED, "", [
            ("zsh takes 2s to start, profile it", "run"),
            ("lazy-load nvm", "edit", [".zshrc"]),
            ("what else is slow?", "chat"),
            ("drop the unused plugins", "edit", [".zshrc"]),
        ], closed=60 * 25, updated=60 * 26, reply="Startup is 180ms now, down from 2.1s."),
    ]
    for s in out:
        if s.title.startswith("Dark mode"):
            s.ringing = "finished"  # just finished; the phone is ringing
    out.sort(key=_sort_key)
    return out


class DemoStore:
    demo = True
    days = 3.0
    state = None

    def __init__(self):
        self.show_all = False
        self._sessions = sessions()

    def refresh(self) -> list[Session]:
        self.collisions = collide.annotate(self._sessions)
        return self._sessions
