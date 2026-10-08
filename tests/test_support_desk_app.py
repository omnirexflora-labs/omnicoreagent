"""The support desk app (``apps/support_desk``), end to end and in process.

The real agent, built the way OmniServe builds it, served by a real
``OmniServe``, talking to the fake provider over HTTP through ``base_url``.
A customer asks about an order, then for a refund; the run pauses for a
person; a person approves over HTTP; the run resumes; the refund ledger gets
exactly one row; the trace shows the ask and the approval.
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omnicoreagent import OmniServe, OmniServeConfig
from test_fake_provider import running_fake_provider

DESK = Path(__file__).resolve().parent.parent / "apps" / "support_desk"


def _load_desk():
    spec = importlib.util.spec_from_file_location("support_desk_agent", DESK / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """The desk served in process, against the fake provider; yields (client, module)."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DESK_BASE_URL", raising=False)
    monkeypatch.setenv("DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'memory.db'}")
    # The trace store lives under the working directory.
    monkeypatch.chdir(tmp_path)
    module = _load_desk()
    with running_fake_provider() as url:
        monkeypatch.setenv("DESK_FAKE_URL", f"{url}/v1")
        agent = module.create_agent()
        server = OmniServe(agent, OmniServeConfig(request_timeout=60, background_enabled=False))
        with TestClient(server.app) as client:
            client.provider = url
            yield client, module


def _chat(client, text, session="maya"):
    response = client.post("/run/sync", json={"query": text, "session_id": session})
    assert response.status_code == 200, response.text
    return response.json()


def _decide(client, paused, decision="approve", **extra):
    (approval,) = paused["approvals"]
    response = client.post(
        f"/runs/{paused['run_id']}/approvals/{approval['approval_id']}",
        json={"decision": decision, "approver": "dana", **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_refund_waits_for_a_person_then_happens_exactly_once(desk):
    client, module = desk

    # 1. A question about an order is answered from the order, with no pause.
    answer = _chat(client, "Where is order 1042?")
    assert answer["status"] == "success" and answer["approvals"] is None
    assert "shipped" in answer["response"] and "42.00" in answer["response"]
    assert module.refund_ledger() == []

    # 2. A refund request pauses the run before anything moves.
    paused = _chat(client, "Please refund order 1042, $12.50.")
    assert paused["status"] == "awaiting_approval" and paused["response"] is None
    (approval,) = paused["approvals"]
    assert approval["tool_name"] == "issue_refund"
    assert approval["arguments"] == {"order_id": "1042", "amount": 12.5}
    assert "person on the support team" in approval["reason"]
    assert module.refund_ledger() == []

    # 3. A person approves it over HTTP.
    decided = _decide(client, paused, note="Customer called.")
    assert decided["status"] == "approved"

    # 4. The run resumes and finishes.
    resumed = client.post(f"/runs/{paused['run_id']}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "success"
    assert "refund" in resumed.json()["response"].lower()

    # 5. One refund, for what the customer asked.
    ledger = module.refund_ledger()
    assert [(r["order_id"], r["amount"]) for r in ledger] == [("1042", 12.5)]

    # Resuming again does not refund again.
    assert client.post(f"/runs/{paused['run_id']}/resume").status_code == 409
    assert len(module.refund_ledger()) == 1

    # 6. The record shows the ask and the approval.
    story = client.get(f"/runs/{paused['run_id']}/trajectory").json()
    assert [s["status"] for s in story["segments"]] == ["suspended", "completed"]
    assert story["approvals"][0]["approver"] == "dana"
    assert story["approvals"][0]["tool_name"] == "issue_refund"
    outcomes = {c["tool_name"]: c["outcome"] for c in story["tool_calls"]}
    assert outcomes == {"lookup_order": "success", "issue_refund": "success"}

    events = client.get("/telemetry/events", params={"run_id": paused["run_id"], "limit": 500}).json()
    kinds = [e["event_type"] for e in (events["events"] if isinstance(events, dict) else events)]
    assert "run_suspended" in kinds
    assert any("approval" in kind for kind in kinds), kinds


def test_a_denied_refund_moves_no_money(desk):
    client, module = desk
    paused = _chat(client, "Refund order 1044 please.", session="lena")
    assert paused["status"] == "awaiting_approval"
    _decide(client, paused, decision="deny", note="Outside the return window.")
    resumed = client.post(f"/runs/{paused['run_id']}/resume").json()
    assert resumed["status"] == "success"
    assert module.refund_ledger() == []
    assert "not issued" in resumed["response"]


def test_a_chat_streams_over_sse_and_the_default_capture_keeps_model_prompts_out(desk):
    client, _ = desk
    with client.stream("POST", "/run", json={"query": "What is your returns policy?", "session_id": "sse"}) as stream:
        text = "".join(stream.iter_text())
    blocks = [b for b in text.split("\n\n") if b.strip()]
    names = [b.split("\n", 1)[0].removeprefix("event: ") for b in blocks]
    assert names[0] == "session" and names[-1] == "session"
    assert "text_delta" in names and "tool_requested" in names
    complete = json.loads(next(b for b in blocks if b.startswith("event: complete")).split("data: ", 1)[1])
    assert complete["status"] == "success"

    # The trace holds the request and the answer, and the tool calls, but not
    # what the model was sent: the instructions never appear in it.
    trace = json.dumps(client.get(f"/telemetry/runs/{complete['run_id']}/trace").json())
    assert "search_kb" in trace and "support desk assistant" not in trace


def test_prometheus_counts_the_routes(desk):
    client, _ = desk
    _chat(client, "Where is order 1043?", session="omar")
    metrics = client.get("/prometheus")
    assert metrics.status_code == 200 and "omniserve_requests_run_sync_total 1" in metrics.text


def test_the_ledger_only_grows_and_refunds_are_bounded(desk):
    _, module = desk
    first = module.issue_refund("1042", 10)
    second = module.issue_refund("1042", 10)
    assert first["issued"] and second["issued"] and first["refund_id"] != second["refund_id"]
    assert module.issue_refund("1042", 9999)["issued"] is False
    assert module.issue_refund("nope", 1)["issued"] is False
    assert len(module.refund_ledger()) == 2


def test_an_unknown_order_is_not_found(desk):
    _, module = desk
    assert module.lookup_order("9999") == {"found": False, "order_id": "9999"}


def test_the_refund_rule_is_the_only_ask_and_the_budgets_are_set(desk):
    _, module = desk
    policy = module.build_policy()
    assert [r.rule_id for r in policy.rules.ask][0] == "refunds_need_a_person"
    budgets = module.build_budgets()
    assert budgets["application"][0]["window"] == "day"
    assert {b["meter"] for b in budgets["request"]} == {"model_cost_usd", "tool_calls"}


def test_a_key_in_the_environment_points_the_desk_at_the_real_provider(monkeypatch):
    module = _load_desk()
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("DESK_MODEL", "gpt-5.4-mini")
    monkeypatch.delenv("DESK_BASE_URL", raising=False)
    assert "base_url" not in module.build_model_config()
    monkeypatch.delenv("LLM_API_KEY")
    assert module.build_model_config()["api_key"] == "fake"


def test_an_otlp_exporter_is_added_only_when_an_endpoint_is_set(monkeypatch):
    module = _load_desk()
    monkeypatch.delenv("DESK_OTLP_ENDPOINT", raising=False)
    assert module.build_telemetry_exporters() == []
    monkeypatch.setenv("DESK_OTLP_ENDPOINT", "http://jaeger:4318")
    assert module.build_telemetry_exporters()[0]["endpoint"] == "http://jaeger:4318"


def test_the_load_profile_lifts_the_dollar_budgets_and_turns_the_debug_routes_on(monkeypatch):
    # Off by default: the desk a customer reaches has real limits and no debug routes.
    monkeypatch.delenv("DESK_PROFILE", raising=False)
    monkeypatch.delenv("DESK_DEBUG", raising=False)
    plain = _load_desk()
    assert plain.routers == [] and plain.build_budgets()["session"][0]["limit"] == 1.0
    # The harness's profile: budgets that cannot stop a load run, and the routes.
    monkeypatch.setenv("DESK_PROFILE", "load")
    load = _load_desk()
    budgets = load.build_budgets()
    assert budgets["session"][0]["limit"] > 1e6 and budgets["application"][0]["limit"] > 1e6
    assert {b["meter"]: b["limit"] for b in budgets["request"]}["tool_calls"] == 20
    assert [r.prefix for r in load.routers] == ["/_debug"]


def test_the_slow_tool_hooks_are_off_until_set(monkeypatch, tmp_path):
    monkeypatch.setenv("DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.delenv("DESK_TOOL_DELAY", raising=False)
    monkeypatch.delenv("DESK_REFUND_HOLD", raising=False)
    module = _load_desk()
    module.seed_database()
    started = time.monotonic()
    module.lookup_order("1042")
    module.issue_refund("1042", 1)
    assert time.monotonic() - started < 0.5
    module._tool_delay.update(seconds=0.3, refund_hold=0.3)
    started = time.monotonic()
    module.lookup_order("1042")
    assert 0.3 <= time.monotonic() - started < 1.0
    started = time.monotonic()
    module.issue_refund("1042", 2)
    # The hold comes after the ledger row is written: the crash window.
    assert time.monotonic() - started >= 0.3 and len(module.refund_ledger()) == 2


def test_the_lag_probe_sees_a_blocked_event_loop():
    import asyncio

    module = _load_desk()

    async def scenario():
        probe = module.LagProbe()
        probe.read()
        await asyncio.sleep(0.2)
        time.sleep(0.4)  # a blocking call on the loop
        await asyncio.sleep(0.2)
        return probe.read(reset_window=False)

    seen = asyncio.run(scenario())
    assert seen["samples"] >= 1 and seen["window_max_ms"] >= 300 and seen["over_100ms"] >= 1


def test_many_sessions_leave_nothing_behind_in_the_process(desk):
    """Server soak, 2026-10-07: memory rose 2.6 to 2.8 MiB a minute with no plateau.

    The agent kept a session's state (its messages and loop detector) for every
    session that ever ran. After a warm-up, a crowd of new sessions doing the
    desk's flows (a lookup, a help question, a refund through approval and
    resume) must not leave their state, or anything else that counts per
    session, in the heap. Counted in objects, not bytes: deterministic, and
    the regex scans a byte tracer would slow down are not in the way.
    """
    import gc
    from collections import Counter

    client, _ = desk

    def visit(number: int) -> None:
        session = f"soak-{number}"
        if number % 3 == 0:
            _chat(client, "Where is order 1042?", session=session)
        elif number % 3 == 1:
            _chat(client, "What is your returns policy?", session=session)
        else:
            paused = _chat(client, "Please refund order 1042, $12.50.", session=session)
            _decide(client, paused)
            assert client.post(f"/runs/{paused['run_id']}/resume").status_code == 200

    def census() -> Counter:
        gc.collect()
        return Counter(type(o).__name__ for o in gc.get_objects())

    # The first visits fill the bounded caches (URL parsing, token counts) and
    # import what is loaded on first use, so they are not measured. The
    # interpreter's own bookkeeping (containers, timers, weak references) comes
    # and goes between collections; what a leak leaves is a class of ours, once
    # per visit.
    noise = {
        "dict", "list", "tuple", "set", "frozenset", "cell", "function", "method", "lock",
        "builtin_function_or_method", "ReferenceType", "TimerHandle", "Context", "hamt",
        "hamt_bitmap_node", "LogRecord", "SplitResult", "str", "bytes", "int", "float",
    }
    for number in range(90):
        visit(number)
    before = census()
    for number in range(90, 150):
        visit(number)
    after = census()

    grown = {
        name: after[name] - before[name]
        for name in after
        if name not in noise and after[name] - before[name] >= 30
    }
    # Sixty visits: anything kept per visit shows as 60 or more of its type.
    assert grown == {}, grown
