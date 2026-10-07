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

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from omnicoreagent import MemoryRouter, OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy

APPLICATION_ID = "support-desk"

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


# --- the tools --------------------------------------------------------------------

tools = ToolRegistry()


@tools.register_tool(name="lookup_order", idempotent=True)
def lookup_order(order_id: str) -> dict:
    """Look up an order by its id: who ordered it, what it is, its status and total."""
    with closing(_connect()) as db:
        row = db.execute("SELECT * FROM orders WHERE order_id = ?", (str(order_id).removeprefix("ORD-"),)).fetchone()
        if row is None:
            return {"found": False, "order_id": order_id}
        refunded = db.execute("SELECT COALESCE(SUM(amount), 0) FROM refunds WHERE order_id = ?", (row["order_id"],)).fetchone()[0]
    return {**dict(row), "found": True, "refunded": refunded}


@tools.register_tool(name="search_kb", idempotent=True)
def search_kb(query: str) -> dict:
    """Search the help articles. Returns the articles that mention the words of the query."""
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
            "tool_call_timeout": 30,
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


__all__ = ["create_agent", "refund_ledger", "tools"]
