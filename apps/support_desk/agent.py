"""The support desk: one agent that answers customers, served to many at once.

Served by OmniServe (``omniserve run --agent apps/support_desk/agent.py``).
Each customer has a session and chats over HTTP. The agent looks up orders,
searches help articles and, when a customer asks for money back, asks a person
before it refunds anything: the refund is the one thing here that cannot be
taken back, so the policy pauses the run until someone approves it over HTTP.

The policy, the budgets and the model are written below, in one place. The
model is the fake provider (``fakeprovider/``) unless ``LLM_API_KEY`` is set.

See ``engineering/architecture/production-readiness-plan.md``.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone

from omnicoreagent import MemoryRouter, OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy

APPLICATION_ID = "support-desk"

# ``DESK_PROFILE=load`` is the profile the load and chaos harnesses run the
# desk under. The fake provider answers under a priced model name, so its runs
# charge the budgets at real prices and a few hundred of them would stop the
# load with budget pauses that say nothing about the runtime. The load profile
# lifts the dollar limits (never the tool-call cap) and turns the debug
# routes on. It is never the default.
PROFILE = os.environ.get("DESK_PROFILE", "")
LOAD_PROFILE = PROFILE == "load"

# --- the data: a small seeded SQLite file ----------------------------------------

ORDERS = [
    ("1042", "Maya Chen", "shipped", 42.00, "Wireless mouse"),
    ("1043", "Omar Haddad", "processing", 129.99, "Desk lamp"),
    ("1044", "Lena Fischer", "delivered", 18.50, "USB-C cable"),
    ("2001", "Tom Alvarez", "delivered", 249.00, "Standing desk mat"),
    ("2002", "Priya Nair", "cancelled", 59.90, "Keyboard"),
]
ARTICLES = [
    ("shipping", "Orders ship within two business days. A tracking link is emailed when the parcel leaves."),
    ("returns", "You can return an item within 30 days of delivery. Refunds go back to the original payment method."),
    ("refunds", "A refund is issued by a person on the support team and arrives in 5 to 7 days."),
    ("warranty", "Every product has a one-year warranty against defects."),
    ("password", "To reset a password, use the 'Forgot password' link on the sign-in page."),
]


def database_path() -> str:
    return os.environ.get("DESK_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "desk.db"))


def _connect() -> sqlite3.Connection:
    path = database_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # A busy timeout, not the default of none: a request that finds the file
    # locked by another one waits its turn instead of failing.
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def seed_database() -> None:
    """Create the tables and the seed rows once; leave an existing ledger alone."""
    with closing(_connect()) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, customer TEXT, status TEXT, total REAL, item TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS articles (topic TEXT PRIMARY KEY, body TEXT)")
        # The ledger only ever grows: a refund is a row, never an update.
        db.execute(
            "CREATE TABLE IF NOT EXISTS refunds (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "order_id TEXT, amount REAL, created_at TEXT)"
        )
        db.executemany("INSERT OR IGNORE INTO orders VALUES (?, ?, ?, ?, ?)", ORDERS)
        db.executemany("INSERT OR IGNORE INTO articles VALUES (?, ?)", ARTICLES)


def refund_ledger() -> list[dict]:
    """Every refund issued so far, oldest first."""
    with closing(_connect()) as db:
        return [dict(row) for row in db.execute("SELECT id, order_id, amount, created_at FROM refunds ORDER BY id")]


# --- test hooks (off by default) ---------------------------------------------------

# A slow tool, for the chaos harness: ``DESK_TOOL_DELAY`` seconds before
# ``lookup_order`` and ``search_kb`` answer. It can be changed while the desk
# runs through ``POST /_debug/tool_delay`` (only when debug routes are on).
# ``refund_hold`` (``DESK_REFUND_HOLD``) is the other hook: ``issue_refund``
# waits that long *after* its ledger row is committed and before it returns.
# That is the crash window that matters for a call that is not idempotent
# (the effect happened, the run has not yet recorded it), so the chaos
# harness kills the desk inside it.
_tool_delay = {
    "seconds": float(os.environ.get("DESK_TOOL_DELAY", "0") or 0),
    "refund_hold": float(os.environ.get("DESK_REFUND_HOLD", "0") or 0),
}


def _slow() -> None:
    # The tools run in a worker thread, so sleeping here holds up one tool
    # call, not the event loop.
    if _tool_delay["seconds"] > 0:
        time.sleep(_tool_delay["seconds"])


# --- the tools --------------------------------------------------------------------

tools = ToolRegistry()


@tools.register_tool(name="lookup_order", idempotent=True)
def lookup_order(order_id: str) -> dict:
    """Look up an order by its id: who ordered it, what it is, its status and total."""
    _slow()
    with closing(_connect()) as db:
        row = db.execute("SELECT * FROM orders WHERE order_id = ?", (str(order_id).removeprefix("ORD-"),)).fetchone()
        if row is None:
            return {"found": False, "order_id": order_id}
        refunded = db.execute("SELECT COALESCE(SUM(amount), 0) FROM refunds WHERE order_id = ?", (row["order_id"],)).fetchone()[0]
    return {**dict(row), "found": True, "refunded": refunded}


@tools.register_tool(name="search_kb", idempotent=True)
def search_kb(query: str) -> dict:
    """Search the help articles. Returns the articles that mention the words of the query."""
    _slow()
    words = [w for w in query.lower().replace("?", " ").split() if len(w) > 2]
    with closing(_connect()) as db:
        rows = [dict(r) for r in db.execute("SELECT topic, body FROM articles")]
    hits = [r for r in rows if any(w in (r["topic"] + " " + r["body"]).lower() for w in words)]
    return {"articles": hits[:3] or rows[:1]}


@tools.register_tool(name="issue_refund")
def issue_refund(order_id: str, amount: float) -> dict:
    """Refund an order, in dollars. The money moves: ask a person first (the policy does)."""
    # Not idempotent, on purpose: running it twice refunds twice. That is what
    # the chaos tests look for, and why a run that died mid-call is told the
    # outcome is unknown rather than quietly retried.
    with closing(_connect()) as db, db:
        row = db.execute("SELECT total FROM orders WHERE order_id = ?", (str(order_id),)).fetchone()
        if row is None:
            return {"issued": False, "reason": f"No order {order_id}."}
        if amount <= 0 or amount > row["total"]:
            return {"issued": False, "reason": f"The amount must be above 0 and at most {row['total']:.2f}."}
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = db.execute("INSERT INTO refunds (order_id, amount, created_at) VALUES (?, ?, ?)", (str(order_id), amount, created_at))
    if _tool_delay["refund_hold"] > 0:
        time.sleep(_tool_delay["refund_hold"])
    return {"issued": True, "refund_id": cursor.lastrowid, "order_id": str(order_id), "amount": amount}


# --- the policy, the budgets, the model --------------------------------------------


def build_policy():
    """Everything the agent does is allowed, except refunds, which ask a person."""
    policy = build_default_policy("permissive-dev")
    policy.rules.ask.insert(
        0,
        PolicyRule(
            rule_id="refunds_need_a_person",
            effect=PolicyEffect.ASK,
            capability="tool.local.call",
            target={"tool_name": "issue_refund"},
            reason="A refund moves money and cannot be undone: a person on the support team approves it.",
        ),
    )
    return policy


def build_budgets() -> dict:
    if LOAD_PROFILE:
        return {
            "application_id": APPLICATION_ID,
            "application": [{"meter": "model_cost_usd", "limit": 1e9, "window": "day"}],
            "session": [{"meter": "model_cost_usd", "limit": 1e9}],
            "request": [
                {"meter": "model_cost_usd", "limit": 1e9},
                {"meter": "tool_calls", "limit": int(os.environ.get("DESK_REQUEST_TOOL_CALLS", "20"))},
            ],
        }
    return {
        "application_id": APPLICATION_ID,
        # What the whole desk may spend in a day.
        "application": [
            {"meter": "model_cost_usd", "limit": float(os.environ.get("DESK_DAILY_USD", "5.00")),
             "window": "day", "warn_at": 0.8},
        ],
        # What one customer's conversation may spend.
        "session": [
            {"meter": "model_cost_usd", "limit": float(os.environ.get("DESK_SESSION_USD", "1.00"))},
        ],
        # What one request may spend: a runaway loop stops here.
        "request": [
            {"meter": "model_cost_usd", "limit": float(os.environ.get("DESK_REQUEST_USD", "0.50")), "warn_at": 0.5},
            {"meter": "tool_calls", "limit": int(os.environ.get("DESK_REQUEST_TOOL_CALLS", "20"))},
        ],
    }


def build_model_config() -> dict:
    """The fake provider when there is no key; the real one when there is.

    ``LLM_API_KEY`` is the runtime's key variable, so a real run needs only it
    (and ``DESK_MODEL``, which names the model). ``DESK_BASE_URL`` points a
    real run at a gateway; without a key, ``DESK_FAKE_URL`` is where the fake
    provider listens.
    """
    model = os.environ.get("DESK_MODEL", "gpt-5.4-mini")
    config = {"provider": os.environ.get("DESK_PROVIDER", "openai"), "model": model, "max_tokens": 1000}
    base_url = os.environ.get("DESK_BASE_URL")
    if not os.environ.get("LLM_API_KEY"):
        # The model name stays a real one: a dollar budget needs a published price.
        config["base_url"] = os.environ.get("DESK_FAKE_URL", "http://127.0.0.1:9000/v1")
        config["api_key"] = "fake"
    elif base_url:
        config["base_url"] = base_url
    return config


SYSTEM = """You are the support desk assistant for an online shop.

- When a customer names an order, look it up before you say anything about it.
- For questions about shipping, returns or the shop, search the help articles.
- Only refund when the customer asks for money back. Use the order's id, and the
  amount the customer asks for, or the order's total if they name none.
- A refund is approved by a person. If it is not approved, say so plainly and do
  not promise it again.
- Answer in plain text, in two or three short sentences.
"""


def build_telemetry_exporters() -> list[dict]:
    """An OTLP exporter, only when an endpoint is set (for example a Jaeger collector)."""
    endpoint = os.environ.get("DESK_OTLP_ENDPOINT")
    return [{"destination": "otlp", "endpoint": endpoint, "service_name": "support-desk"}] if endpoint else []


def create_agent() -> OmniCoreAgent:
    """Build the agent. OmniServe calls this once, when it loads the file."""
    seed_database()
    # Postgres (DATABASE_URL) holds the conversations, the run records and the
    # budget counters. Without it, they live in this process: fine to try the
    # desk, but a restart forgets everything.
    memory = MemoryRouter("sql" if os.environ.get("DATABASE_URL") else "in_memory")
    return OmniCoreAgent(
        name="support-desk",
        system_instruction=SYSTEM,
        model_config=build_model_config(),
        local_tools=tools,
        memory_router=memory,
        agent_config={
            "max_steps": 10,
            "tool_call_timeout": int(os.environ.get("DESK_TOOL_TIMEOUT", "30")),
            "run_lease_seconds": int(os.environ.get("DESK_LEASE_SECONDS", "60")),
            "governance_config": {
                "enabled": True,
                "policy": build_policy(),
                "budgets": build_budgets(),
                # A refund waits for a person; the run is saved and resumed
                # after the approval, even by another process.
                "approval_mode": "suspend",
            },
        },
        # The default capture keeps tool calls and results but not model
        # prompts or responses: customers' words stay out of the traces.
        telemetry_config={"capture": "default"},
        telemetry_exporters=build_telemetry_exporters(),
    )


# --- debug routes (off by default) ----------------------------------------------------
#
# The harnesses need three things the runtime does not expose: the refund
# ledger (to prove each refund happened exactly once), the event loop's lag (a
# stall shows here first), and a way to slow the tools. They sit behind the
# API token like every other route, and exist only when ``DESK_DEBUG=1`` or
# the load profile is on.

DEBUG = LOAD_PROFILE or os.environ.get("DESK_DEBUG") == "1"


class LagProbe:
    """Measures how late the event loop wakes a sleeping task.

    A task that sleeps 50 ms and wakes 700 ms late proves something held the
    loop for about 650 ms. It is a task on the server's own loop, started by
    the first call to ``/_debug/lag`` (the harness makes it at the start).
    """

    INTERVAL = 0.05

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.reset_all()

    def reset_all(self) -> None:
        self.samples = 0
        self.max_ms = 0.0
        self.over_100ms = 0
        self.over_500ms = 0
        self.window_max_ms = 0.0
        self.started_at = time.time()

    def ensure_running(self) -> None:
        if self.task is None or self.task.done():
            self.started_at = time.time()
            self.task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        while True:
            before = time.perf_counter()
            await asyncio.sleep(self.INTERVAL)
            lag_ms = max(0.0, (time.perf_counter() - before - self.INTERVAL) * 1000)
            self.samples += 1
            self.max_ms = max(self.max_ms, lag_ms)
            self.window_max_ms = max(self.window_max_ms, lag_ms)
            self.over_100ms += lag_ms > 100
            self.over_500ms += lag_ms > 500

    def read(self, *, reset_window: bool = True) -> dict:
        self.ensure_running()
        view = {
            "samples": self.samples, "max_ms": round(self.max_ms, 1),
            "window_max_ms": round(self.window_max_ms, 1),
            "over_100ms": self.over_100ms, "over_500ms": self.over_500ms,
            "running_seconds": round(time.time() - self.started_at, 1),
        }
        if reset_window:
            self.window_max_ms = 0.0
        return view


lag_probe = LagProbe()


# --- the census (debug routes only) ---------------------------------------------------
#
# Soak 2 (2026-10-08) still grew about 25 KB a visit in the real stack, with
# both earlier fixes in. The in-process measurements said nothing, so the desk
# can now say what it holds: the objects by type, the size of every cache and
# registry that is ours, the SQLAlchemy pools, and what the C allocator holds.
# Taken twice in a soak and diffed, it names what grows per visit.

# Per class: the attributes whose size is worth a number. Found by walking the
# heap, not by reaching into the server, so a registry the desk cannot see
# from here is still counted.
_REGISTRIES = {
    "AgentSessionStateStore": ["states"],
    "OmniCoreAgent": ["_active_run_ids", "_fresh_run_ids", "_claim_heartbeats"],
    "TelemetryRecorder": [
        "_span_parent_contexts", "_span_sources", "_incomplete_trace_ids",
        "_trace_templates", "_trace_span_ids", "_context_recordings",
    ],
    "InMemoryTelemetryStore": [
        "_traces", "_trace_sequences", "_event_index", "_event_cursors",
        "_indexed_cursors", "_unsorted", "_subscribers",
    ],
    "JsonlTelemetryStore": ["_finished", "_pending"],
    "PrivacyFilter": ["_cache"],
    "RunAdmission": ["_waiters"],
    "OrphanSweeper": ["_resuming", "_claims"],
    "_SeenEvents": ["_ids", "_order"],
}

_ARENA_BYTES = 64 * 1024 * 1024  # glibc's HEAP_MAX_SIZE on 64-bit Linux


def _mapped_arena_heaps() -> int | None:
    """The 64 MB heaps glibc has mapped for its non-main arenas, or None off Linux.

    A heap is a 64 MB-aligned stretch of address space of which the front is
    readable and writable and the rest reserved (no access). The main arena
    lives on the program break and is not counted.
    """
    try:
        with open("/proc/self/maps") as maps:
            rows = []
            for line in maps:
                parts = line.split()
                low, high = (int(x, 16) for x in parts[0].split("-"))
                rows.append((low, high, parts[1], len(parts) > 5))
    except OSError:
        return None
    heaps = 0
    for index, (low, high, perms, named) in enumerate(rows):
        if named or perms != "rw-p" or low % _ARENA_BYTES:
            continue
        size = high - low
        following = rows[index + 1] if index + 1 < len(rows) else None
        if following and following[0] == high and following[2] == "---p":
            size += following[1] - following[0]
        if size == _ARENA_BYTES:
            heaps += 1
    return heaps


def _allocator() -> dict:
    import ctypes

    from omnicoreagent.serve import malloc

    view: dict = {
        "malloc_arena_max_env": os.environ.get("MALLOC_ARENA_MAX"),
        "mallopt_applied": getattr(malloc, "applied_arenas", "unknown"),
        "malloc_arenas_mapped": _mapped_arena_heaps(),
    }
    try:
        class Mallinfo2(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_size_t)
                for name in (
                    "arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks",
                    "fsmblks", "uordblks", "fordblks", "keepcost",
                )
            ]

        libc = ctypes.CDLL("libc.so.6")
        libc.mallinfo2.restype = Mallinfo2
        info = libc.mallinfo2()
        # What malloc holds from the system, how much of it is handed out, and
        # how much is free but kept: free-but-kept is fragmentation, not a leak.
        view["malloc"] = {
            "system_bytes": info.arena, "in_use_bytes": info.uordblks,
            "free_bytes": info.fordblks, "mmapped_bytes": info.hblkhd,
            "free_chunks": info.ordblks,
        }
    except (OSError, AttributeError):
        view["malloc"] = None
    return view


def _rss_bytes() -> int:
    try:
        with open("/proc/self/statm") as statm:
            return int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def _size_of(value) -> int | str:
    try:
        return len(value)
    except TypeError:
        return "no len"


def take_census() -> dict:
    """What this process holds right now, for a soak to diff."""
    import gc
    import sys
    import threading
    from collections import Counter

    gc.collect()
    objects = gc.get_objects()
    by_type = Counter(type(o).__name__ for o in objects)
    registries: dict = {}
    pools: list = []
    engines = 0
    for obj in objects:
        name = type(obj).__name__
        attrs = _REGISTRIES.get(name)
        if attrs and type(obj).__module__.startswith(("omnicoreagent", "support_desk")):
            for attr in attrs:
                size = _size_of(getattr(obj, attr, None))
                if isinstance(size, int):
                    key = f"{name}.{attr}"
                    registries[key] = registries.get(key, 0) + size
            registries[f"{name}.instances"] = registries.get(f"{name}.instances", 0) + 1
        elif name in ("QueuePool", "NullPool", "StaticPool", "SingletonThreadPool"):
            try:
                pools.append({"pool": name, "status": obj.status()})
            except Exception as exc:  # a pool mid-dispose still gets counted
                pools.append({"pool": name, "status": f"{type(exc).__name__}"})
        elif name == "Engine":
            engines += 1
    del objects

    caches: dict = {}
    from omnicoreagent.core.summarizer import tokenizer
    from omnicoreagent.core.telemetry import redaction
    from omnicoreagent.core.workspace import factory

    caches["tokenizer._count_cache"] = len(tokenizer._count_cache)
    caches["workspace.factory._backend_cache"] = len(factory._backend_cache)
    for function in ("_key_words", "_decide_key"):
        cached = getattr(redaction, function, None)
        if cached is not None and hasattr(cached, "cache_info"):
            caches[f"redaction.{function}"] = cached.cache_info().currsize
    registries.update(caches)

    return {
        "rss_bytes": _rss_bytes(),
        "allocated_blocks": sys.getallocatedblocks(),
        "threads": threading.active_count(),
        "allocator": _allocator(),
        "gc_top": by_type.most_common(40),
        "gc_total_objects": sum(by_type.values()),
        "registries": registries,
        "sql_pools": pools,
        "sql_engines": engines,
    }


routers: list = []
if DEBUG:
    from fastapi import APIRouter, HTTPException

    debug_router = APIRouter(prefix="/_debug", tags=["Debug"])

    @debug_router.get("/lag")
    async def debug_lag() -> dict:
        return lag_probe.read()

    @debug_router.get("/ledger")
    async def debug_ledger() -> dict:
        # The ledger is a SQLite read: a thread keeps it off the event loop.
        return {"refunds": await asyncio.to_thread(refund_ledger)}

    @debug_router.get("/census")
    async def debug_census() -> dict:
        return take_census()

    @debug_router.post("/tool_delay")
    async def debug_tool_delay(body: dict) -> dict:
        for key in _tool_delay:
            if key in body:
                _tool_delay[key] = float(body[key])
        if "seconds" not in body and "refund_hold" not in body:
            raise HTTPException(status_code=422, detail="Send seconds and/or refund_hold.")
        return dict(_tool_delay)

    routers.append(debug_router)


__all__ = ["create_agent", "refund_ledger", "routers", "tools"]
