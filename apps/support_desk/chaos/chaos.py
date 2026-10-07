"""Chaos test for the support desk: load, with one scripted fault at a time.

    python apps/support_desk/chaos/chaos.py --rounds 14 --users 30
    python apps/support_desk/chaos/chaos.py --faults desk_kill,provider_429 --rounds 2
    python apps/support_desk/chaos/chaos.py --list

Each round runs about 30 simulated customers (and the staff who approve
their refunds) against the desk, injects one fault, holds it, clears it,
keeps the load going a little longer, then stops the load and waits for the
desk to settle. Then it asserts, for the round:

1. no refund (the one call that is not idempotent) appears twice in the
   ledger, and none was issued that nobody approved;
2. every run ends completed, failed with a reason, or waiting for a person,
   within its lease plus two minutes of the fault ending;
3. when the fault was a 429, whether the runtime waited as long as
   ``Retry-After`` said, read from the fake provider's own request log.

It acts only on the containers of one Compose project (``--project``), found
by label and named by service, and only on ``desk``, ``postgres``, ``redis``;
the model's faults go through the fake provider's ``/_control``.

The runtime does not resume a run whose process died: nothing calls
``resume`` by itself. A person or a sweeper does. So after the fault the
harness plays that operator (``--no-operator`` turns it off): it resumes
runs whose lease has lapsed and decides refunds nobody decided. How many
runs needed it is in the report, because it is the design's cost.

The model is the fake provider: nothing here spends money. The desk must run
with ``DESK_PROFILE=load`` (see ../load/README.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "load"))

from loadlib import (  # noqa: E402
    Desk, Load, check_ledger, container_id, docker, error_table, fake_get, find_stuck,
    find_waiting, list_runs, prime_fake, read_ledger,
)

LEASE_GRACE = 120  # the plan: its lease plus 2 minutes


# --- the faults ----------------------------------------------------------------------


@dataclass
class Ctx:
    """What a fault may touch: one Compose project, and the fake provider."""

    project: str
    desk: Desk
    fake_url: str
    args: argparse.Namespace

    def container(self, service: str, *, running_only: bool = False) -> str:
        cid = container_id(self.project, service, running_only=running_only)
        if not cid:
            raise RuntimeError(f"no container for service {service!r} in Compose project {self.project!r}")
        return cid

    async def docker(self, *a, timeout=60.0) -> str:
        return await asyncio.to_thread(docker, *a, timeout=timeout)

    async def kill(self, service: str) -> None:
        await self.docker("kill", self.container(service, running_only=True))

    async def ensure_started(self, service: str, *, wait: float = 4.0) -> None:
        """The restart policy usually brings a killed container back; give it
        a moment, and start it ourselves if it did not."""
        deadline = time.time() + wait
        while time.time() < deadline:
            if container_id(self.project, service, running_only=True):
                return
            await asyncio.sleep(0.5)
        await self.docker("start", self.container(service))

    async def wait_ready(self, timeout: float = 150.0) -> float:
        """Until the desk answers ``/ready``; returns how long it took."""
        started = time.time()
        while time.time() - started < timeout:
            body, error = await self.desk.quiet("GET", "/ready", timeout=5)
            if error is None and body and body.get("ready"):
                return time.time() - started
            await asyncio.sleep(1)
        raise RuntimeError("the desk did not become ready")

    async def wait_healthy(self, service: str, timeout: float = 90.0) -> None:
        started = time.time()
        while time.time() - started < timeout:
            cid = container_id(self.project, service, running_only=True)
            if cid:
                state = await self.docker("inspect", "-f", "{{.State.Health.Status}}", cid, timeout=15)
                if state == "healthy":
                    return
            await asyncio.sleep(1)
        raise RuntimeError(f"{service} did not become healthy")

    async def fake(self, **settings):
        return await prime_fake(self.fake_url, **settings)


@dataclass
class Fault:
    name: str
    describe: str
    hold: float                      # seconds the fault stays in place
    inject: object                   # async (ctx) -> None
    clear: object = None             # async (ctx) -> None
    retry_after: float | None = None  # set when the round asserts Retry-After
    stream_share: float | None = None  # a fault that only shows on streamed chats


async def _kill_desk(ctx: Ctx) -> None:
    await ctx.kill("desk")
    await ctx.ensure_started("desk")
    await ctx.wait_ready()


async def _kill_desk_in_refund(ctx: Ctx) -> None:
    # The refund waits 8 s after its ledger row is committed and before it
    # returns: the window in which the effect has happened and the run does
    # not know it. Kill inside it, so the question is real.
    await ctx.desk.quiet("POST", "/_debug/tool_delay", json={"refund_hold": 8})
    await asyncio.sleep(14)
    await _kill_desk(ctx)


async def _restart(ctx: Ctx, service: str) -> None:
    await ctx.docker("restart", "-t", "2", ctx.container(service), timeout=90)
    await ctx.wait_healthy(service)


async def _kill_and_start(ctx: Ctx, service: str) -> None:
    await ctx.kill(service)
    await ctx.ensure_started(service)
    await ctx.wait_healthy(service)


async def _provider(ctx: Ctx, **settings) -> None:
    await ctx.fake(**settings)


async def _provider_clear(ctx: Ctx) -> None:
    await ctx.fake(rate_429=0, rate_500=0, rate_timeout=0, rate_slow_stream=0)


async def _tool_delay(ctx: Ctx, seconds: float) -> None:
    await ctx.desk.quiet("POST", "/_debug/tool_delay", json={"seconds": seconds})


async def _tool_clear(ctx: Ctx) -> None:
    await ctx.desk.quiet("POST", "/_debug/tool_delay", json={"seconds": 0, "refund_hold": 0})


def faults(args: argparse.Namespace) -> dict[str, Fault]:
    items = [
        Fault("desk_kill", "docker kill of the desk mid-run, then a restart", 0, _kill_desk),
        Fault("desk_kill_in_refund",
              "docker kill of the desk while refunds sit between their ledger write and their return", 0, _kill_desk_in_refund),
        Fault("postgres_restart", "restart of Postgres mid-run (run state, memory, budgets)", 0,
              lambda c: _restart(c, "postgres")),
        Fault("postgres_kill", "docker kill of Postgres mid-run, then a start", 0, lambda c: _kill_and_start(c, "postgres")),
        Fault("redis_restart", "restart of Redis (the background task store)", 0, lambda c: _restart(c, "redis")),
        Fault("provider_429", "the model provider answers 429 with Retry-After: 3 to a third of calls", 40,
              lambda c: _provider(c, rate_429=0.33, retry_after=3), _provider_clear, retry_after=3),
        Fault("provider_500", "the model provider answers 500 to a third of calls", 40,
              lambda c: _provider(c, rate_500=0.33), _provider_clear),
        Fault("provider_hang", "a tenth of model calls hang far past any timeout", 30,
              lambda c: _provider(c, rate_timeout=0.1, hang_seconds=args.hang_seconds), _provider_clear),
        Fault("provider_slow_stream", "streamed model replies pause 1.5 s between chunks", 40,
              lambda c: _provider(c, rate_slow_stream=1.0, slow_stream_delay=1.5), _provider_clear, stream_share=0.8),
        Fault("slow_tool", "every order lookup and help search takes 8 s", 40,
              lambda c: _tool_delay(c, 8), _tool_clear),
        Fault("tool_timeout", "every order lookup and help search outlasts the 30 s tool timeout", 45,
              lambda c: _tool_delay(c, args.tool_timeout + 5), _tool_clear),
    ]
    return {f.name: f for f in items}


# --- the operator ----------------------------------------------------------------------


class Operator:
    """What a person on call does after a fault: resume what lost its process,
    decide the refunds nobody decided. It counts what it had to do."""

    def __init__(self, desk: Desk, load: Load, tag: str) -> None:
        self.desk, self.load, self.tag = desk, load, tag
        self.resumed: dict[str, dict] = {}     # run_id -> what happened
        self.decided: dict[str, str] = {}
        self.waiting_tries: dict[str, int] = {}

    def _attempt(self, session: str):
        return next((a for a in self.load.attempts if a.session == session), None)

    async def sweep(self) -> None:
        now = datetime.now(timezone.utc)
        for run in await list_runs(self.desk, "running"):
            if not str(run.get("session_id", "")).startswith(self.tag) or not run.get("heartbeat_at"):
                continue
            age = (now - datetime.fromisoformat(run["heartbeat_at"])).total_seconds()
            if age <= (run.get("lease_seconds") or 60) + 2 or run["run_id"] in self.resumed:
                continue
            body, error = await self.desk.call("operator_resume", "POST", f"/runs/{run['run_id']}/resume",
                                               expect=(200,), detail=run["session_id"], timeout=200)
            self.resumed[run["run_id"]] = {"t": time.time(), "session": run["session_id"], "error": error,
                                           "status": (body or {}).get("status"), "lease_age_s": round(age)}
            attempt = self._attempt(run["session_id"])
            if attempt is not None and body and body.get("status") == "success":
                attempt.outcome = "completed"
        for run in await find_waiting(self.desk, self.tag):
            run_id = run["run_id"]
            # A refund the staff are about to decide is theirs; the operator
            # takes what has waited a while, and tries a run a few times at most.
            idle = (now - datetime.fromisoformat(run["updated_at"])).total_seconds() if run.get("updated_at") else 999
            if idle < 20 or self.waiting_tries.get(run_id, 0) >= 3:
                continue
            self.waiting_tries[run_id] = self.waiting_tries.get(run_id, 0) + 1
            story, error = await self.desk.quiet("GET", f"/runs/{run_id}/trajectory", timeout=60)
            attempt = self._attempt(run["session_id"])
            decision = attempt.decision if attempt else "deny"
            for approval in [a for a in (story or {}).get("approvals", []) if a.get("status") == "pending"]:
                self.decided[run_id] = decision
                await self.desk.call("operator_approve", "POST", f"/runs/{run_id}/approvals/{approval['approval_id']}",
                                     expect=(200, 409), detail=run["session_id"],
                                     json={"decision": decision, "approver": "oncall", "note": "after the fault"})
            # Whether it was decided just now or earlier (and the resume was
            # lost to the fault), the run still waits: resume it.
            body, error = await self.desk.call("operator_resume", "POST", f"/runs/{run_id}/resume", expect=(200, 409),
                                               detail=run["session_id"], timeout=200)
            self.resumed.setdefault(run_id, {"t": time.time(), "session": run["session_id"], "error": error,
                                             "status": (body or {}).get("status"), "after": "approval"})
            if attempt is not None and body and body.get("status") == "success":
                attempt.outcome = "completed"


async def refund_calls_cut_off(desk: Desk, operator: Operator, load: Load) -> list[dict]:
    """Refunds whose process died inside the call: what became of each.

    A run the operator resumed has an ``interrupted`` segment where its
    process went away. If ``issue_refund`` was under way in it, the effect may
    have happened without the run knowing: the case the plan asks about.
    """
    found = []
    for run_id, info in list(operator.resumed.items())[:60]:
        if info.get("after") == "approval":
            continue
        story, error = await desk.quiet("GET", f"/runs/{run_id}/trajectory", timeout=60)
        if error or not story:
            continue
        for segment in story.get("segments", []):
            trajectory = segment.get("trajectory") or {}
            calls = [c for step in trajectory.get("steps", []) for c in step.get("tool_calls", []) if c.get("tool_name") == "issue_refund"]
            if segment.get("status") == "interrupted" and calls:
                final = next((c for c in story["tool_calls"] if c["tool_name"] == "issue_refund"), {})
                attempt = next((a for a in load.attempts if a.session == info["session"]), None)
                found.append({"run_id": run_id, "outcome_in_the_killed_segment": calls[0].get("outcome"),
                              "final_state": final.get("state"), "final_outcome": final.get("outcome"),
                              "order_id": attempt.order_id if attempt else None, "cents": attempt.cents if attempt else None})
                break
    return found


async def reason_from_trace(desk: Desk, run_id: str) -> str | None:
    """Why a run ended badly, from its trace: the first error an event carries."""
    body, error = await desk.quiet("GET", "/telemetry/events", params={"run_id": run_id, "limit": 500}, timeout=60)
    if error:
        return None
    events = body["events"] if isinstance(body, dict) else body
    for event in sorted(events, key=lambda e: e.get("sequence_number", 0)):
        problem = event.get("error")
        if problem:
            return f"{event['event_type']}: {problem.get('type')}: {problem.get('message')}"
    return None


# --- one round ---------------------------------------------------------------------------


def _overlaps(record, start, end) -> bool:
    return record.t0 <= end and record.t0 + record.seconds >= start


def retry_after_respected(timings: list[dict], tolerance: float = 0.3) -> dict:
    """After each 429, did the next request of the same conversation wait as long as asked?"""
    by_conversation: dict[str, list[dict]] = {}
    for t in timings:
        by_conversation.setdefault(t["conv"], []).append(t)
    checked, respected, gaps, no_retry = 0, 0, [], 0
    for rows in by_conversation.values():
        rows.sort(key=lambda t: t["t_in"])
        for i, row in enumerate(rows):
            if row["status"] != 429 or row["t_out"] is None:
                continue
            following = [r for r in rows[i + 1:] if r["t_in"] >= row["t_out"] - 0.05]
            if not following:
                no_retry += 1
                continue
            gap = following[0]["t_in"] - row["t_out"]
            checked += 1
            gaps.append(round(gap, 2))
            respected += gap + tolerance >= row["retry_after"]
    return {
        "429s_seen": sum(1 for t in timings if t["status"] == 429), "retries_checked": checked,
        "waited_at_least_retry_after": respected, "429s_with_no_retry_after_them": no_retry,
        "min_gap_s": min(gaps) if gaps else None, "median_gap_s": sorted(gaps)[len(gaps) // 2] if gaps else None,
        "respected": (respected == checked) if checked else None,
    }


async def run_round(ctx: Ctx, fault: Fault, number: int, args: argparse.Namespace) -> dict:
    desk = ctx.desk
    desk.records.clear()
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    tag = f"chaos{number:02d}{stamp}"
    print(f"[chaos] round {number}: {fault.name} ({fault.describe})", flush=True)
    await ctx.fake(reset=True, latency_min=args.model_latency[0], latency_max=args.model_latency[1],
                   rate_429=0, rate_500=0, rate_timeout=0, rate_slow_stream=0)
    await _tool_clear(ctx)
    baseline = await read_ledger(desk) or []
    load = Load(
        desk, tag=tag, stream_share=fault.stream_share if fault.stream_share is not None else 0.2,
        kb_share=0.3 if fault.stream_share is None else 0.6,
        think=(0.5, 1.5), used_amounts={(r["order_id"], round(r["amount"] * 100)) for r in baseline},
    )
    load.start_staff(max(2, args.users // 5))
    load.add_users(args.users)
    await asyncio.sleep(args.warmup)

    t_start = time.time()
    injected_error = None
    try:
        await fault.inject(ctx)
        if fault.hold:
            await asyncio.sleep(fault.hold)
    except Exception as exc:  # noqa: BLE001 - a fault that cannot be injected is a harness failure
        injected_error = f"{type(exc).__name__}: {exc}"
        print(f"[chaos]   INJECTION FAILED: {injected_error}", flush=True)
    finally:
        if fault.clear:
            await fault.clear(ctx)
    t_end = time.time()
    print(f"[chaos]   fault in place {t_end - t_start:.0f}s; load goes on {args.after}s", flush=True)

    operator = Operator(desk, load, tag)
    # The operator starts once the fault is over, as a person would.
    stop_operator = asyncio.Event()

    async def operate():
        while not stop_operator.is_set():
            try:
                await operator.sweep()
            except Exception as exc:  # noqa: BLE001
                print(f"[chaos]   operator error: {exc}", flush=True)
            await asyncio.sleep(10)

    operating = asyncio.create_task(operate()) if args.operator else None
    await asyncio.sleep(args.after)
    await load.stop(drain=args.drain)

    # Settle: until nothing is stuck, or the plan's deadline.
    lease = args.lease
    deadline = t_end + lease + LEASE_GRACE
    stuck = []
    while True:
        stuck = await find_stuck(desk, tag)
        waiting = await find_waiting(desk, tag)
        if not stuck and (not waiting or not args.operator):
            break
        if time.time() > deadline:
            break
        await asyncio.sleep(5)
    settled_at = time.time()
    stop_operator.set()
    if operating:
        await operating

    # What became of every session the fault touched.
    window = (t_start - 2, t_end + 2)
    touched = {r.detail for r in desk.records if r.detail and (_overlaps(r, *window) or (r.error and r.t0 >= t_start))}
    touched |= {lost["session"] for lost in load.lost if lost["t1"] >= window[0] and lost["t0"] <= window[1]}
    touched = {s for s in touched if s.startswith(tag)}
    affected, statuses = [], {}
    sem = asyncio.Semaphore(8)

    async def runs_of(session):
        async with sem:
            body, error = await desk.quiet("GET", "/runs", params={"session_id": session, "limit": 20}, timeout=60)
        return [] if error else body["runs"]

    for runs in await asyncio.gather(*(runs_of(s) for s in sorted(touched))):
        for run in runs:
            statuses[run["status"]] = statuses.get(run["status"], 0) + 1
            affected.append({
                "run_id": run["run_id"], "session_id": run["session_id"], "status": run["status"],
                "attempt": run.get("attempt"), "error": run.get("error"),
                "lease_seconds": run.get("lease_seconds"), "heartbeat_at": run.get("heartbeat_at"),
                "updated_at": run.get("updated_at"), "trace_ids": run.get("trace_ids"),
                "resumed_by_operator": run["run_id"] in operator.resumed,
            })

    # All runs of the round, to check each one's end state.
    all_by_status: dict[str, list[dict]] = {}
    for status in ("running", "interrupted", "awaiting_budget", "failed", "timeout", "blocked", "cancelled", "abandoned", "awaiting_approval"):
        mine = [r for r in await list_runs(desk, status) if str(r.get("session_id", "")).startswith(tag)]
        if mine:
            all_by_status[status] = mine
    # A run that did not complete owes an explanation. The run's own record
    # is the first place to look; the trace is the second.
    ended_badly = [r for s in ("failed", "timeout", "blocked", "cancelled", "abandoned") for r in all_by_status.get(s, [])]
    failed_without_reason_in_record = [r["run_id"] for r in ended_badly if not r.get("error")]
    reasons = {}
    for run in ended_badly:
        reasons[run["run_id"]] = run.get("error") or await reason_from_trace(desk, run["run_id"])
    failed_without_reason = [run_id for run_id, reason in reasons.items() if not reason]
    # A refund whose resume call was cut off by the fault but whose run did
    # finish is a completed refund: the run's record is the truth.
    final_status = {a["run_id"]: a["status"] for a in affected}
    for attempt in load.attempts:
        if attempt.run_id and attempt.outcome == "not_completed" and final_status.get(attempt.run_id) == "completed":
            attempt.outcome = "completed"
    ledger = await read_ledger(desk)
    ledger_check = (check_ledger(load.attempts, ledger, {r["id"] for r in baseline})
                    if ledger is not None else {"ok": False, "error": "could not read the ledger"})

    cut_off = await refund_calls_cut_off(desk, operator, load)
    timings = (await fake_get(ctx.fake_url, "/_timings", since=t_start - 1) or {}).get("timings", [])
    retry = retry_after_respected(timings) if fault.retry_after else None
    errors_in = [r for r in desk.records if r.error and window[0] <= r.t0 <= window[1] + args.after]
    ok_runs_after = sum(1 for a in load.attempts if a.outcome == "completed")
    result = {
        "round": number, "fault": fault.name, "describe": fault.describe, "tag": tag,
        "t_start": t_start, "t_end": t_end, "fault_seconds": round(t_end - t_start, 1),
        "settle_seconds": round(settled_at - t_end, 1), "deadline_seconds": lease + LEASE_GRACE,
        "injection_error": injected_error,
        "users": args.users, "requests": len(desk.records),
        "client_errors": error_table([r for r in desk.records if not r.kind.endswith("_first_byte")]),
        "client_errors_during_and_after_fault": len(errors_in),
        "affected_runs": affected, "affected_run_count": len(affected), "affected_statuses": statuses,
        "operator": {"resumed": operator.resumed, "decided": operator.decided,
                     "runs_needing_resume": sum(1 for v in operator.resumed.values() if v.get("after") != "approval"),
                     "waiting_runs_resumed": sum(1 for v in operator.resumed.values() if v.get("after") == "approval"),
                     "refunds_needing_a_decision": len(operator.decided),
                     "refund_calls_cut_off": cut_off},
        "stuck": stuck, "refunds_waiting_for_a_person_at_end": len(await find_waiting(desk, tag)),
        "failed_runs_without_a_reason": failed_without_reason,
        "failed_runs_without_a_reason_in_the_run_record": failed_without_reason_in_record,
        "reasons": {run_id: str(reason)[:200] for run_id, reason in list(reasons.items())[:20]},
        "other_end_states": {k: len(v) for k, v in all_by_status.items()},
        "ledger": ledger_check, "retry_after": retry, "refunds_completed": ok_runs_after,
        "fake_stats": await fake_get(ctx.fake_url, "/_stats"),
    }
    result["assertions"] = {
        # The hard promise about the one call that is not idempotent.
        "no_refund_twice_and_none_unapproved": bool(ledger_check.get("ok")),
        "no_run_stuck_past_lease_plus_2min": not stuck,
        "failed_runs_have_a_reason": not failed_without_reason,
        # None when the round was not about a 429.
        "retry_after_respected": None if retry is None else retry["respected"],
        "fault_injected": injected_error is None,
    }
    result["passed"] = all(value is not False for value in result["assertions"].values())
    print(f"[chaos]   {'PASS' if result['passed'] else 'FAIL'}: {len(affected)} runs touched, "
          f"{len(operator.resumed)} resumed by the operator, stuck {len(stuck)}, "
          f"ledger problems {ledger_check.get('problems') or 'none'}", flush=True)
    return result


# --- the whole thing -----------------------------------------------------------------------


def arguments(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rounds", type=int, default=22, help="how many fault rounds in all, taken in turn from --faults")
    p.add_argument("--faults", help="comma-separated fault names (default: all); --list shows them")
    p.add_argument("--list", action="store_true")
    p.add_argument("--users", type=int, default=30)
    p.add_argument("--warmup", type=float, default=20)
    p.add_argument("--after", type=float, default=30, help="seconds of load after the fault is cleared")
    p.add_argument("--drain", type=float, default=150, help="most seconds to let visits under way finish")
    p.add_argument("--lease", type=int, default=int(os.environ.get("DESK_LEASE_SECONDS", "60")),
                   help="the desk's run lease (DESK_LEASE_SECONDS), for the deadline")
    p.add_argument("--tool-timeout", type=int, default=int(os.environ.get("DESK_TOOL_TIMEOUT", "30")))
    p.add_argument("--hang-seconds", type=float, default=200)
    p.add_argument("--target-affected", type=int, default=50, help="the plan's number of chaos runs")
    p.add_argument("--model-latency", default="0.5,1.5")
    p.add_argument("--no-operator", dest="operator", action="store_false")
    p.add_argument("--base-url", default=os.environ.get("DESK_URL", "http://127.0.0.1:8800"))
    p.add_argument("--fake-url", default=os.environ.get("DESK_FAKE_URL_PUBLIC", "http://127.0.0.1:9000"))
    p.add_argument("--token", default=os.environ.get("OMNICOREAGENT_SERVE_AUTH_TOKEN", "change-me"))
    p.add_argument("--project", default=os.environ.get("DESK_PROJECT", "support-desk"))
    p.add_argument("--out")
    args = p.parse_args(argv)
    args.model_latency = tuple(float(x) for x in args.model_latency.split(","))
    return args


async def run_chaos(args: argparse.Namespace) -> dict:
    desk = Desk(args.base_url, args.token)
    ready, error = await desk.quiet("GET", "/ready")
    lag, lag_error = await desk.quiet("GET", "/_debug/lag")
    if error or lag_error:
        raise SystemExit("The desk is not ready, or runs without DESK_PROFILE=load (no /_debug routes).")
    catalogue = faults(args)
    names = args.faults.split(",") if args.faults else list(catalogue)
    unknown = [n for n in names if n not in catalogue]
    if unknown:
        raise SystemExit(f"unknown fault(s) {unknown}; --list shows them")
    ctx = Ctx(args.project, desk, args.fake_url, args)
    rounds = []
    started = time.time()
    for number in range(1, args.rounds + 1):
        fault = catalogue[names[(number - 1) % len(names)]]
        try:
            rounds.append(await run_round(ctx, fault, number, args))
        except Exception as exc:  # noqa: BLE001 - one broken round must not hide the others
            rounds.append({"round": number, "fault": fault.name, "passed": False, "harness_error": f"{type(exc).__name__}: {exc}",
                           "affected_runs": [], "affected_run_count": 0})
            print(f"[chaos]   HARNESS ERROR in round {number}: {exc}", flush=True)
            for service in ("desk", "postgres", "redis"):
                if not container_id(args.project, service, running_only=True):
                    await ctx.docker("start", container_id(args.project, service))
            await asyncio.sleep(10)
        await asyncio.sleep(5)
    await desk.close()
    affected = sum(r["affected_run_count"] for r in rounds)
    summary = {
        "rounds": len(rounds), "passed": sum(1 for r in rounds if r["passed"]),
        "affected_runs": affected, "target_affected_runs": args.target_affected,
        "runs_needing_operator_resume": sum(r.get("operator", {}).get("runs_needing_resume", 0) for r in rounds),
        "stuck_runs": sum(len(r.get("stuck", [])) for r in rounds),
        "duplicated_refunds": sum(len(r.get("ledger", {}).get("problems", {}).get("duplicated", [])) for r in rounds),
        "seconds": round(time.time() - started),
    }
    summary["refund_calls_cut_off"] = sum(len(r.get("operator", {}).get("refund_calls_cut_off", [])) for r in rounds)
    if summary["passed"] != summary["rounds"]:
        summary["finish_line_3"] = "MISS"
    elif summary["runs_needing_operator_resume"] and args.operator:
        # Everything ended well, but only because something resumed the runs
        # the faults orphaned: the runtime does not do that by itself.
        summary["finish_line_3"] = (f"MISS without an operator ({summary['runs_needing_operator_resume']} runs were left running "
                                    "until someone resumed them); PASS with one")
    elif affected < args.target_affected:
        summary["finish_line_3"] = f"PASS (only {affected} runs touched, target {args.target_affected})"
    else:
        summary["finish_line_3"] = "PASS"
    return {"summary": summary, "rounds": rounds,
            "config": {k: v for k, v in vars(args).items() if k != "token"}}


def render(result: dict) -> str:
    s = result["summary"]
    lines = [
        "# Support desk chaos report", "",
        f"{s['rounds']} rounds, {s['passed']} passed, {s['affected_runs']} runs touched by a fault "
        f"(plan target {s['target_affected_runs']}), {s['seconds']} s in all. "
        f"Duplicated refunds: {s['duplicated_refunds']}. Runs left stuck: {s['stuck_runs']}. "
        f"Runs left `running` until an operator resumed them: {s['runs_needing_operator_resume']}. "
        f"Refund calls whose process died inside the call: {s['refund_calls_cut_off']}.", "",
        f"Finish line 3 (resilience): **{s['finish_line_3']}**", "",
        "| Round | Fault | Result | Runs touched | End states of touched runs | Operator resumed | Stuck | Ledger | Retry-After |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in result["rounds"]:
        if "harness_error" in r:
            lines.append(f"| {r['round']} | {r['fault']} | **HARNESS ERROR** | | {r['harness_error']} | | | | |")
            continue
        ra = r["retry_after"]
        retry = "n/a" if ra is None else (f"respected ({ra['retries_checked']} retries, min gap {ra['min_gap_s']} s)" if ra["respected"]
                                          else f"{ra['waited_at_least_retry_after']}/{ra['retries_checked']} waited long enough, "
                                               f"{ra['429s_with_no_retry_after_them']} not retried (min gap {ra['min_gap_s']} s)")
        problems = r["ledger"].get("problems") or "ok"
        lines.append(
            f"| {r['round']} | {r['fault']} | **{'PASS' if r['passed'] else 'FAIL'}** | {r['affected_run_count']} | "
            f"{r['affected_statuses']} | {r['operator']['runs_needing_resume']} | {len(r['stuck'])} | {problems} | {retry} |")
    lines += ["", "## What each fault did", ""]
    seen = set()
    for r in result["rounds"]:
        if "harness_error" in r or r["fault"] in seen:
            continue
        seen.add(r["fault"])
        lines += [f"### {r['fault']}", "", f"{r['describe']}. Held {r['fault_seconds']} s; settled {r['settle_seconds']} s after it ended "
                  f"(deadline {r['deadline_seconds']} s).", "",
                  f"- Client errors by kind: {r['client_errors'] or 'none'}",
                  f"- The provider saw: {(r['fake_stats'] or {}).get('by_status')}, faults {(r['fake_stats'] or {}).get('faults')}",
                  f"- Other end states of this round's runs: {r['other_end_states'] or 'none'}",
                  f"- Failed runs with no reason anywhere: {len(r['failed_runs_without_a_reason'])}; "
                  f"with no `error` in the run record (the reason is only in the trace): {len(r['failed_runs_without_a_reason_in_the_run_record'])}",
                  f"- Refunds still waiting for a person at the end: {r['refunds_waiting_for_a_person_at_end']}", ""]
    lines.append("")
    return "\n".join(lines)


async def main() -> int:
    args = arguments()
    if args.list:
        for name, fault in faults(args).items():
            print(f"{name:22} {fault.describe}")
        return 0
    result = await run_chaos(args)
    out = Path(args.out or Path(__file__).parent / "results" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2, default=str))
    (out / "report.md").write_text(render(result))
    print(render(result))
    print(f"[chaos] wrote {out}/result.json and report.md")
    return 0 if result["summary"]["finish_line_3"].startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
