"""The pieces the load, chaos and observability harnesses share.

``Desk`` is a client of the support desk that never raises: every call is
timed and recorded, and a failure becomes a labelled error instead of an
exception, so one dead connection cannot end a run that is meant to measure
failure. ``Load`` simulates customers and the staff who approve their
refunds. ``Sampler`` watches the desk's container and database from outside.
``check_ledger`` and ``find_stuck`` are the correctness checks.

Everything talks to the desk over HTTP, and to Docker only for the
containers of one Compose project, by label. The model is the fake
provider, so nothing here spends money.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

ORDERS = ["1042", "1043", "1044", "2001", "2002"]
# Refund amounts come from these two orders, a unique one per refund, so a row
# in the ledger can be matched to the run that made it. Order 2001 allows up
# to 249.00, order 1043 up to 129.99.
REFUND_ORDERS = (("2001", 24900), ("1043", 12999))

TERMINAL = {"completed", "failed", "blocked", "cancelled", "timeout", "abandoned"}
# What a run may end as and not be "stuck": finished, or waiting for a person.
SETTLED = TERMINAL | {"awaiting_approval"}


def percentile(values: list[float], q: float) -> float | None:
    """The q-th percentile (0-100) by linear interpolation; ``None`` when empty."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * q / 100
    low, high = math.floor(rank), math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def summarize(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50": percentile(values, 50), "p95": percentile(values, 95), "p99": percentile(values, 99),
        "max": max(values) if values else None,
        "mean": statistics.fmean(values) if values else None,
    }


# --- the client -------------------------------------------------------------------


class RetryAfter:
    """Retries a 503 that carries ``Retry-After``, as a real client does.

    The ramp at 100 users (2026-10-07) found the admission limit answering
    some resume calls 503 while this client gave up on the first, leaving 119
    approved runs never resumed. Each wait is the header plus a little jitter,
    so a crowd refused together does not return together; a request gives up
    when the next wait would pass ``budget`` seconds in total. Three counts are
    kept apart: 503s seen, requests that succeeded after a retry, and requests
    that ended on a 503.
    """

    def __init__(self, *, budget: float = 60.0, jitter: float = 0.5, sleep=asyncio.sleep,
                 clock=time.monotonic, rng=random) -> None:
        self.budget, self.jitter = budget, jitter
        self._sleep, self._clock, self._rng = sleep, clock, rng
        self.seen_503 = self.succeeded_after_retry = self.gave_up = 0
        self.by_kind: dict[str, dict[str, int]] = {}

    def _count(self, kind: str | None, key: str) -> None:
        setattr(self, key, getattr(self, key) + 1)
        if kind is not None:
            counts = self.by_kind.setdefault(kind, {"seen_503": 0, "succeeded_after_retry": 0, "gave_up": 0})
            counts[key] += 1

    @staticmethod
    def _asked_to_wait(response) -> float | None:
        try:
            seconds = float(response.headers.get("Retry-After"))
        except (TypeError, ValueError):
            return None
        return seconds if seconds >= 0 else None

    async def run(self, send: Callable[[], Awaitable], *, kind: str | None = None):
        """Call ``send`` until it is not refused with a retryable 503.

        ``send`` returns anything with ``status_code`` and ``headers``.
        Returns ``(response, retries)``.
        """
        began, retries = self._clock(), 0
        while True:
            response = await send()
            if response.status_code != 503:
                if retries and response.status_code < 400:
                    self._count(kind, "succeeded_after_retry")
                return response, retries
            self._count(kind, "seen_503")
            wait = self._asked_to_wait(response)
            if wait is not None:
                wait += self._rng.uniform(0, self.jitter)
            if wait is None or self._clock() - began + wait > self.budget:
                self._count(kind, "gave_up")
                return response, retries
            await self._sleep(wait)
            retries += 1

    def summary(self) -> dict:
        return {
            "seen_503": self.seen_503,
            "succeeded_after_retry": self.succeeded_after_retry,
            "gave_up": self.gave_up,
            "by_kind": self.by_kind,
        }


@dataclass
class Record:
    """One request, as the client saw it."""

    kind: str
    t0: float            # wall clock, seconds since the epoch
    seconds: float       # how long it took, client side
    http: int | None     # status code, or None when there was no response
    error: str | None    # None when the request did what the harness expected
    stage: int = 0
    detail: str = ""
    retries: int = 0     # how many times a 503 with Retry-After was waited out


class Desk:
    """A client of the support desk that records every call."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 150.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(timeout, connect=10.0),
            limits=httpx.Limits(max_connections=1000, max_keepalive_connections=200),
        )
        self.records: list[Record] = []
        self.stage = 0
        self.retry = RetryAfter()

    async def close(self) -> None:
        await self.client.aclose()

    def _note(self, kind, t0, started, http, error, detail="", retries=0) -> Record:
        record = Record(kind, t0, time.perf_counter() - started, http, error, self.stage, detail, retries)
        self.records.append(record)
        return record

    async def call(self, kind: str, method: str, path: str, *, expect=(200,), record=True, detail="", check=None, **kwargs):
        """Make one request. Returns ``(json_or_None, error_or_None)``; never raises."""
        t0, started = time.time(), time.perf_counter()
        retries = 0
        try:
            response, retries = await self.retry.run(
                lambda: self.client.request(method, path, **kwargs), kind=kind
            )
        except httpx.TimeoutException:
            error, http, body = "timeout", None, None
        except httpx.ConnectError:
            error, http, body = "connect", None, None
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError):
            error, http, body = "disconnect", None, None
        except httpx.HTTPError as exc:
            error, http, body = f"client_{type(exc).__name__}", None, None
        else:
            http = response.status_code
            try:
                body = response.json()
            except ValueError:
                body = None
            error = None if http in expect else f"http_{http}"
            if error is None and body is None and response.content:
                error = "bad_json"
            if error is None and check is not None and body is not None:
                error = check(body)
        if record:
            self._note(kind, t0, started, http, error, detail, retries)
        return body, error

    async def quiet(self, method: str, path: str, **kwargs):
        """A call the harness itself makes (not a user's): not recorded."""
        return await self.call("harness", method, path, record=False, **kwargs)

    async def stream_chat(self, kind: str, query: str, session: str):
        """Chat over SSE. Records the whole stream's time; returns the ``complete`` event."""
        t0, started = time.time(), time.perf_counter()
        first = None
        complete = None
        error = None
        http = None
        retries = 0

        async def attempt():
            nonlocal first, complete
            first, complete = None, None
            async with self.client.stream("POST", "/run", json={"query": query, "session_id": session}) as response:
                if response.status_code == 200:
                    event = None
                    async for line in response.aiter_lines():
                        if first is None:
                            first = time.perf_counter() - started
                        if line.startswith("event: "):
                            event = line[7:].strip()
                        elif line.startswith("data: ") and event == "complete":
                            complete = json.loads(line[6:])
                return response

        try:
            response, retries = await self.retry.run(attempt, kind=kind)
            http = response.status_code
            if http != 200:
                error = f"http_{http}"
        except httpx.TimeoutException:
            error = "timeout"
        except httpx.ConnectError:
            error = "connect"
        except (httpx.RemoteProtocolError, httpx.ReadError):
            error = "disconnect"
        except httpx.HTTPError as exc:
            error = f"client_{type(exc).__name__}"
        if error is None and complete is None:
            error = "stream_without_complete"
        if error is None and complete.get("status") != "success":
            error = f"run_{complete.get('status')}"
        self._note(kind, t0, started, http, error, session, retries)
        if first is not None and error is None:
            self.records.append(Record(kind + "_first_byte", t0, first, http, None, self.stage))
        return complete, error


# --- customers and staff ------------------------------------------------------------


@dataclass
class Attempt:
    """One refund a customer asked for: the unit of the exactly-once check."""

    order_id: str
    cents: int
    session: str
    decision: str                  # "approve" or "deny"
    run_id: str | None = None
    outcome: str = "pending"       # completed | not_completed | approve_failed | no_pause
    t_asked: float = 0.0
    t_done: float | None = None
    # The staff's resume never got through (a 503 that outlasted the retries),
    # and the server's orphan sweep resumed the run and finished it.
    recovered_by_sweep: bool = False


@dataclass
class Job:
    attempt: Attempt
    approval_id: str
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


def _run_ended_well(body: dict) -> str | None:
    """A chat's answer is an error unless the run finished or paused for a person."""
    status = body.get("status")
    return None if status in ("success", "awaiting_approval") else f"run_{status}"


class Load:
    """Customers who chat, and staff who approve their refunds."""

    def __init__(
        self,
        desk: Desk,
        *,
        tag: str,
        kb_share: float = 0.3,
        stream_share: float = 0.2,
        deny_share: float = 0.1,
        think: tuple[float, float] = (1.0, 3.0),
        approve_delay: tuple[float, float] = (0.3, 1.5),
        used_amounts: set | None = None,
        seed: int | None = None,
    ) -> None:
        self.desk, self.tag = desk, tag
        self.kb_share, self.stream_share, self.deny_share = kb_share, stream_share, deny_share
        self.think, self.approve_delay = think, approve_delay
        self.random = random.Random(seed)
        self.stopping = False
        self.users: list[asyncio.Task] = []
        self.staff: list[asyncio.Task] = []
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self.attempts: list[Attempt] = []
        self.run_ids: dict[str, dict] = {}       # run_id -> what the client knows of it
        self.lost: list[dict] = []               # chats that never returned a run id
        self.visits = 0
        self.completed_visits = 0
        self._used = set(used_amounts or ())
        self._candidates = self._candidate_amounts()
        self.uid = 0

    def _candidate_amounts(self):
        # A shuffled walk over every (order, cents) pair not yet in the ledger.
        pairs = [(order, cents) for order, top in REFUND_ORDERS for cents in range(1, top + 1)]
        self.random.shuffle(pairs)
        return iter(pairs)

    def _next_amount(self) -> tuple[str, int]:
        for order, cents in self._candidates:
            if (order, cents) not in self._used:
                self._used.add((order, cents))
                return order, cents
        raise RuntimeError("out of unique refund amounts")

    # staff -------------------------------------------------------------------

    def start_staff(self, count: int) -> None:
        self.staff += [asyncio.create_task(self._staff()) for _ in range(count)]

    async def _staff(self) -> None:
        while True:
            job = await self.queue.get()
            attempt = job.attempt
            try:
                await asyncio.sleep(self.random.uniform(*self.approve_delay))
                body = {"decision": attempt.decision, "approver": "dana", "note": "load test"}
                if attempt.decision == "deny":
                    body["note"] = "Outside the return window."
                decided = await self._retry("approve", "POST", f"/runs/{attempt.run_id}/approvals/{job.approval_id}", session=attempt.session, json=body)
                if decided is None:
                    attempt.outcome = "approve_failed"
                    continue
                resumed = await self._retry("resume", "POST", f"/runs/{attempt.run_id}/resume", session=attempt.session, tries=2)
                if resumed is not None and resumed.get("status") == "success":
                    attempt.outcome = "completed"
                else:
                    attempt.outcome = "not_completed"
            finally:
                attempt.t_done = time.time()
                if not job.done.done():
                    job.done.set_result(attempt.outcome)

    async def _retry(self, kind, method, path, *, session, tries=3, **kwargs):
        # A person whose click failed clicks again. A repeated approve or
        # resume is safe: the runtime answers 409 for a decision already made.
        for attempt in range(tries):
            body, error = await self.desk.call(kind, method, path, expect=(200,), detail=session, **kwargs)
            if error is None:
                return body
            if error == "http_409":
                return body or {"status": "already"}
            await asyncio.sleep(2 + attempt * 2)
        return None

    # customers ---------------------------------------------------------------

    def add_users(self, count: int) -> None:
        for _ in range(count):
            self.uid += 1
            self.users.append(asyncio.create_task(self._user(self.uid)))

    async def _user(self, uid: int) -> None:
        cycle = 0
        while not self.stopping:
            cycle += 1
            try:
                await self._visit(uid, cycle)
            except Exception as exc:  # noqa: BLE001 - a harness bug must show, not end the user
                self.desk._note("harness_error", time.time(), time.perf_counter(), None, f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(self.random.uniform(*self.think))

    def _track(self, run_id, session, kind):
        if run_id:
            self.run_ids.setdefault(run_id, {"session": session, "kind": kind, "t": time.time()})

    async def _chat(self, kind: str, query: str, session: str, *, stream=False):
        t0 = time.time()
        if stream:
            body, error = await self.desk.stream_chat(kind, query, session)
        else:
            body, error = await self.desk.call(
                kind, "POST", "/run/sync", detail=session, check=_run_ended_well,
                json={"query": query, "session_id": session},
            )
        run_id = (body or {}).get("run_id")
        self._track(run_id, session, kind)
        if run_id is None:
            self.lost.append({"session": session, "kind": kind, "t0": t0, "t1": time.time(), "error": error})
        return body, error

    async def _visit(self, uid: int, cycle: int) -> None:
        self.visits += 1
        session = f"{self.tag}-u{uid}-c{cycle}"
        ref = f"ref u{uid}c{cycle}"
        order = self.random.choice(ORDERS)
        _, error = await self._chat("chat_order", f"Where is order {order}? ({ref}a)", session)
        if error:
            return
        if self.random.random() < self.kb_share:
            stream = self.random.random() < self.stream_share
            _, error = await self._chat(
                "chat_kb_stream" if stream else "chat_kb",
                f"What is your returns policy? ({ref}b)", session, stream=stream,
            )
            if not error:
                self.completed_visits += 1
            return
        refund_order, cents = self._next_amount()
        attempt = Attempt(
            refund_order, cents, session,
            "deny" if self.random.random() < self.deny_share else "approve", t_asked=time.time(),
        )
        self.attempts.append(attempt)
        body, error = await self._chat(
            "chat_refund", f"Please refund order {refund_order}, ${cents / 100:.2f}. ({ref}c)", session
        )
        attempt.run_id = (body or {}).get("run_id")
        if error or body.get("status") != "awaiting_approval" or not body.get("approvals"):
            attempt.outcome = "no_pause" if not error else "not_completed"
            attempt.t_done = time.time()
            return
        job = Job(attempt, body["approvals"][0]["approval_id"])
        self.queue.put_nowait(job)
        try:
            await asyncio.wait_for(job.done, timeout=300)
        except asyncio.TimeoutError:
            attempt.outcome = "not_completed"
            return
        if attempt.outcome == "completed":
            self.completed_visits += 1

    # control -----------------------------------------------------------------

    async def stop(self, drain: float = 240.0) -> None:
        """Stop starting new visits; let the ones under way finish, then cancel."""
        self.stopping = True
        if self.users:
            _, pending = await asyncio.wait(self.users, timeout=drain)
            for task in pending:
                task.cancel()
        for task in self.staff:
            task.cancel()
        await asyncio.gather(*self.users, *self.staff, return_exceptions=True)


# --- the view from outside -----------------------------------------------------------


def docker(*args: str, timeout: float = 30.0) -> str:
    """Run ``docker`` with a timeout; return stdout, or "" on any failure."""
    try:
        done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def container_id(project: str, service: str, *, running_only=True) -> str:
    """The container of ``service`` in the Compose project, found by label."""
    args = ["ps", "-q" if running_only else "-aq",
            "--filter", f"label=com.docker.compose.project={project}",
            "--filter", f"label=com.docker.compose.service={service}"]
    return docker(*args).split("\n")[0]


_UNITS = {"B": 1 / 1024**2, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "kB": 1 / 1024, "MB": 1, "GB": 1024}


def _mebibytes(text: str) -> float | None:
    found = re.match(r"([\d.]+)\s*([A-Za-z]+)", text or "")
    return float(found.group(1)) * _UNITS.get(found.group(2), 1) if found else None


class Sampler:
    """Every few seconds: the desk container's memory and CPU, Postgres
    connections, and the event loop's worst lag since the last sample."""

    def __init__(self, desk: Desk, project: str, *, interval: float = 5.0) -> None:
        self.desk, self.project, self.interval = desk, project, interval
        self.samples: list[dict] = []
        self._task: asyncio.Task | None = None
        self.started = time.time()

    def start(self) -> None:
        self.started = time.time()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            begun = time.time()
            self.samples.append(await self.sample())
            await asyncio.sleep(max(0.5, self.interval - (time.time() - begun)))

    async def sample(self) -> dict:
        row: dict = {"t": time.time(), "elapsed": round(time.time() - self.started, 1)}
        desk_id, pg_id = await asyncio.gather(
            asyncio.to_thread(container_id, self.project, "desk"),
            asyncio.to_thread(container_id, self.project, "postgres"),
        )
        row["desk_up"] = bool(desk_id)
        if desk_id:
            stats = await asyncio.to_thread(docker, "stats", "--no-stream", "--format", "{{json .}}", desk_id, timeout=20)
            try:
                parsed = json.loads(stats)
                row["mem_mib"] = _mebibytes(parsed["MemUsage"].split("/")[0])
                row["cpu_pct"] = float(parsed["CPUPerc"].rstrip("%"))
                row["pids"] = int(parsed.get("PIDs") or 0)
            except (ValueError, KeyError):
                pass
        if pg_id:
            out = await asyncio.to_thread(
                docker, "exec", pg_id, "psql", "-U", "desk", "-d", "desk", "-tAc",
                "SELECT count(*), count(*) FILTER (WHERE state = 'active') "
                "FROM pg_stat_activity WHERE datname = 'desk'", timeout=15,
            )
            if "|" in out:
                total, active = out.split("|")
                row["pg_connections"], row["pg_active"] = int(total), int(active)
        lag, error = await self.desk.quiet("GET", "/_debug/lag")
        if error is None and lag:
            row["lag_window_max_ms"] = lag["window_max_ms"]
            row["lag_max_ms"] = lag["max_ms"]
            row["lag_over_500ms"] = lag["over_500ms"]
        return row


def summarize_resources(samples: list[dict], *, since: float | None = None) -> dict:
    rows = [s for s in samples if since is None or s["t"] >= since]

    def series(key):
        return [s[key] for s in rows if s.get(key) is not None]

    out = {}
    for key in ("mem_mib", "cpu_pct", "pg_connections", "pg_active", "lag_window_max_ms"):
        values = series(key)
        if values:
            out[key] = {"min": min(values), "median": statistics.median(values), "max": max(values), "last": values[-1]}
    out["samples"] = len(rows)
    out["desk_down_samples"] = sum(1 for s in rows if not s.get("desk_up", True))
    lag = series("lag_window_max_ms")
    out["lag_max_ms"] = max(lag) if lag else None
    return out


# --- what the runtime adds to a step -----------------------------------------------


async def runtime_overhead(desk: Desk, run_ids: list[str], *, concurrency: int = 4) -> dict:
    """The time a run spends on anything but the model, from each run's trajectory.

    A trajectory has one segment per stretch of a run, each with its steps;
    every step has its own duration and every model call in it the latency
    the runtime measured around the provider call (``facts.latency_ms``).
    The overhead of a step is ``step.duration_ms`` minus those latencies:
    what the loop, the policy, the memory writes, the telemetry and the
    tools cost in that step. A tool's own time is included, as the plan
    defines it ("the step time minus the model time"); the tools here are a
    SQLite read and write, and ``tool_ms`` shows how much of it that is.
    ``run_non_model_ms`` is the coarser per-segment figure: the segment's
    whole duration minus its model latency, which also counts the set-up
    before the first step.
    """
    semaphore = asyncio.Semaphore(concurrency)
    steps_model: list[float] = []
    steps_tool_only: list[float] = []
    segments: list[float] = []
    tool_ms: list[float] = []
    model_ms: list[float] = []
    missing_latency = 0
    fetched = 0
    failed = 0

    async def one(run_id):
        nonlocal missing_latency, fetched, failed
        async with semaphore:
            story, error = await desk.quiet("GET", f"/runs/{run_id}/trajectory", timeout=60)
        if error or not story:
            failed += 1
            return
        fetched += 1
        for segment in story.get("segments", []):
            trajectory = segment.get("trajectory") or {}
            totals = trajectory.get("totals") or {}
            if totals.get("duration_ms") is not None and totals.get("model_latency_ms") is not None:
                segments.append(totals["duration_ms"] - totals["model_latency_ms"])
            for step in trajectory.get("steps", []):
                latencies = []
                for call in step.get("model_calls", []):
                    latency = (call.get("facts") or {}).get("latency_ms")
                    if latency is None:
                        missing_latency += 1
                    else:
                        latencies.append(latency)
                if step.get("duration_ms") is None:
                    continue
                model_ms.extend(latencies)
                overhead = step["duration_ms"] - sum(latencies)
                (steps_model if step.get("model_calls") else steps_tool_only).append(overhead)
        for call in story.get("tool_calls", []):
            if call.get("started_at") and call.get("ended_at") and call.get("outcome") == "success":
                from datetime import datetime
                tool_ms.append(
                    (datetime.fromisoformat(call["ended_at"]) - datetime.fromisoformat(call["started_at"])).total_seconds() * 1000
                )

    await asyncio.gather(*(one(run_id) for run_id in run_ids))
    return {
        "runs_read": fetched, "runs_unreadable": failed, "model_calls_without_latency": missing_latency,
        "step_overhead_ms": summarize(steps_model + steps_tool_only),
        "step_overhead_ms_model_steps": summarize(steps_model),
        "step_overhead_ms_tool_only_steps": summarize(steps_tool_only),
        "run_non_model_ms": summarize(segments),
        "tool_ms": summarize(tool_ms),
        "model_latency_ms": summarize(model_ms),
    }


# --- correctness --------------------------------------------------------------------


async def read_ledger(desk: Desk) -> list[dict] | None:
    body, error = await desk.quiet("GET", "/_debug/ledger", timeout=60)
    return None if error else body["refunds"]


async def settle_by_sweep(desk: "Desk", attempts: list[Attempt], *, grace: float, interval: float,
                          poll: float = 5.0, sleep=asyncio.sleep, clock=time.monotonic) -> dict:
    """Give the server's orphan sweep the time it needs before judging refunds.

    An approved run whose resume the client could not get through is not
    broken while the sweep can still resume it (the ramp at 100 users,
    2026-10-07, left 119 such runs; the sweep now resumes a decided run after
    ``grace`` seconds, on a sweep every ``interval``). Each attempt not yet
    completed is read until its run completes (it is then counted completed,
    and marked as recovered by the sweep), ends some other way (nothing will
    complete it), or the wait reaches grace plus one interval. Every run that
    completes extends the wait, so a long backlog is not cut short.
    """
    began = clock()
    window = grace + interval
    cutoff = began + window
    pending = [a for a in attempts if a.run_id and a.outcome == "not_completed"]
    recovered = 0
    while pending:
        answers = await asyncio.gather(*(desk.quiet("GET", f"/runs/{a.run_id}", timeout=60) for a in pending))
        still = []
        for attempt, (body, _) in zip(pending, answers):
            status = (body or {}).get("status")
            if status == "completed":
                attempt.outcome, attempt.recovered_by_sweep, attempt.t_done = "completed", True, time.time()
                recovered += 1
                cutoff = clock() + window
            elif status not in TERMINAL:
                still.append(attempt)
        pending = still
        if not pending or clock() >= cutoff:
            break
        await sleep(min(poll, max(0.0, cutoff - clock())))
    return {
        "waited_seconds": round(clock() - began, 1), "recovered_by_sweep": recovered,
        "still_waiting": [a.run_id for a in pending],
    }


def check_ledger(attempts: list[Attempt], ledger: list[dict], baseline_ids: set) -> dict:
    """Every approved refund is in the ledger exactly once; nothing else is.

    ``attempts`` are the refunds the harness asked for, each with a unique
    (order, amount); ``ledger`` is the desk's ledger now and ``baseline_ids``
    the row ids that were there before the run began.
    """
    rows = [r for r in ledger if r["id"] not in baseline_ids]
    counts = Counter((r["order_id"], round(r["amount"] * 100)) for r in rows)
    known = {(a.order_id, a.cents) for a in attempts}
    problems = {"duplicated": [], "missing_after_completed": [], "issued_without_approval": [],
                "unrequested": [], "issued_but_run_not_completed": []}
    for attempt in attempts:
        n = counts.get((attempt.order_id, attempt.cents), 0)
        who = {"run_id": attempt.run_id, "session": attempt.session, "order_id": attempt.order_id,
               "cents": attempt.cents, "outcome": attempt.outcome}
        if n > 1:
            problems["duplicated"].append({**who, "rows": n})
        if attempt.decision == "approve" and attempt.outcome == "completed" and n != 1:
            problems["missing_after_completed"].append({**who, "rows": n})
        if attempt.decision == "deny" and n:
            problems["issued_without_approval"].append({**who, "rows": n})
        if attempt.decision == "approve" and attempt.outcome in ("no_pause", "approve_failed") and n:
            problems["issued_without_approval"].append({**who, "rows": n})
        if attempt.decision == "approve" and attempt.outcome == "not_completed" and n:
            problems["issued_but_run_not_completed"].append({**who, "rows": n})
    for key in counts:
        if key not in known:
            problems["unrequested"].append({"order_id": key[0], "cents": key[1], "rows": counts[key]})
    approved_completed = sum(1 for a in attempts if a.decision == "approve" and a.outcome == "completed")
    return {
        "attempts": len(attempts), "approved_and_completed": approved_completed,
        "completed_by_sweep": sum(1 for a in attempts if a.recovered_by_sweep),
        "ledger_rows_new": len(rows),
        "outcomes": {f"{d}/{o}": n for (d, o), n in Counter((a.decision, a.outcome) for a in attempts).items()},
        "problems": {k: v for k, v in problems.items() if v},
        # The hard promise: nothing duplicated, nothing issued unapproved. A
        # completed refund missing from the ledger is a promise broken too.
        "ok": not (problems["duplicated"] or problems["issued_without_approval"]
                   or problems["missing_after_completed"] or problems["unrequested"]),
    }


async def list_runs(desk: Desk, status: str, *, limit: int = 500) -> list[dict]:
    body, error = await desk.quiet("GET", "/runs", params={"status": status, "limit": limit}, timeout=60)
    return [] if error else body["runs"]


async def find_stuck(desk: Desk, prefix: str) -> list[dict]:
    """Runs of this test (by session prefix) that are neither finished nor waiting for a person."""
    stuck = []
    for status in ("running", "interrupted", "awaiting_budget"):
        for run in await list_runs(desk, status):
            if str(run.get("session_id", "")).startswith(prefix):
                stuck.append({k: run.get(k) for k in ("run_id", "session_id", "status", "heartbeat_at", "lease_seconds", "updated_at", "attempt")})
    return stuck


async def find_waiting(desk: Desk, prefix: str) -> list[dict]:
    waiting = []
    for run in await list_runs(desk, "awaiting_approval"):
        if str(run.get("session_id", "")).startswith(prefix):
            waiting.append(run)
    return waiting


def error_table(records: list[Record]) -> dict:
    """Errors by kind of request and kind of error."""
    table: dict = defaultdict(Counter)
    for record in records:
        if record.error:
            table[record.error][record.kind] += 1
    return {error: dict(kinds) for error, kinds in sorted(table.items())}


def per_kind(records: list[Record]) -> dict:
    groups: dict = defaultdict(list)
    errors: Counter = Counter()
    for record in records:
        groups[record.kind].append(record.seconds * 1000)
        errors[record.kind] += bool(record.error)
    return {kind: {**summarize(values), "errors": errors[kind]} for kind, values in sorted(groups.items())}


async def prime_fake(fake_url: str, **settings) -> dict | None:
    """Change the fake provider's settings while it runs (and zero its counters)."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.post(f"{fake_url}/_control", json=settings)
            return response.json()
        except httpx.HTTPError:
            return None


async def fake_get(fake_url: str, path: str, **params) -> dict | None:
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(f"{fake_url}{path}", params=params)
            return response.json()
        except httpx.HTTPError:
            return None
