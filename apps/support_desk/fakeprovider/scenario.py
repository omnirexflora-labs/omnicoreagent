"""What the fake model says next, decided from the conversation alone.

The provider keeps no state between requests: the whole conversation arrives
with every call, so the next step is read from it. That keeps it correct when
a run is resumed by another process, or a request is retried after a fault,
and it lets many sessions share one provider without mixing.

The script for one support message:

1. the user names an order: call ``lookup_order``;
2. the user asks for a refund: call ``issue_refund``; otherwise call
   ``search_kb``;
3. answer, from what the tools returned.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

ORDER_ID = re.compile(r"\b(?:ORD-)?(\d{4,6})\b", re.IGNORECASE)
AMOUNT = re.compile(
    r"\$\s?(\d+(?:\.\d{1,2})?)|\b(\d+(?:\.\d{1,2})?)\s*(?:dollars|usd)\b", re.IGNORECASE
)
# The runtime puts a clock line in front of every user message.
CLOCK = re.compile(r"^\s*\[CURRENT_DATETIME:[^\]]*\]\s*")
REFUND_WORDS = ("refund", "money back", "reimburse")


@dataclass
class Step:
    """One model turn: a tool call, or the final answer."""

    tool_name: str | None = None
    arguments: dict = field(default_factory=dict)
    text: str | None = None


def _text(content) -> str:
    # A message's content is a string, or a list of typed parts.
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def _json(content) -> dict:
    try:
        value = json.loads(_text(content))
    except ValueError:
        return {}
    # The runtime may wrap a tool's return value as {"status", "data"}.
    if isinstance(value, dict) and isinstance(value.get("data"), dict):
        return value["data"]
    return value if isinstance(value, dict) else {}


def decide(messages: list[dict], tool_names: set[str]) -> Step:
    """The next step for the conversation so far."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    question = _text(messages[last_user].get("content")) if last_user >= 0 else ""
    question = CLOCK.sub("", question)

    # Only what happened since the person's latest message counts: earlier
    # turns of the session are history, not part of this answer.
    calls: dict[str, str] = {}
    results: dict[str, str] = {}
    for message in messages[last_user + 1 :]:
        for call in message.get("tool_calls") or []:
            calls[call["id"]] = call["function"]["name"]
        if message.get("role") == "tool":
            results[calls.get(message.get("tool_call_id"), "?")] = _text(message.get("content"))

    found_id = ORDER_ID.search(question)
    order_id = found_id.group(1) if found_id else None
    wants_refund = any(word in question.lower() for word in REFUND_WORDS)
    order = _json(results["lookup_order"]) if "lookup_order" in results else {}

    if order_id and "lookup_order" in tool_names and "lookup_order" not in results:
        return Step("lookup_order", {"order_id": order_id})

    if wants_refund and "issue_refund" in tool_names and "issue_refund" not in results:
        found = AMOUNT.search(question)
        amount = float(found.group(1) or found.group(2)) if found else float(order.get("total") or 0)
        return Step(
            "issue_refund",
            {"order_id": order_id or order.get("order_id", ""), "amount": amount},
        )

    if not wants_refund and "search_kb" in tool_names and "search_kb" not in results:
        return Step("search_kb", {"query": question[:200]})

    return Step(text=_answer(order_id, order, results, wants_refund))


def _answer(order_id, order, results, wants_refund) -> str:
    parts = []
    if "lookup_order" in results:
        if order.get("status"):
            total = float(order.get("total", 0))
            parts.append(f"Order {order.get('order_id', order_id)} is {order['status']}, total ${total:.2f}.")
        else:
            parts.append(f"I could not find order {order_id}.")
    if "issue_refund" in results:
        lowered = results["issue_refund"].lower()
        if "denied" in lowered or "not approved" in lowered or "error" in lowered:
            parts.append("The refund was not issued.")
        else:
            parts.append("I have issued the refund.")
    if "search_kb" in results and not wants_refund:
        parts.append("Our help article says orders ship within two business days.")
    return " ".join(parts) or "How can I help you today?"
