"""T3 of the telemetry storage plan: routine checks are summarized, refusals are not.

Measured on the repository steward: 8,812 of its 10,831 policy requests
were the sandbox workspace bridge checking, file by file, that each
workspace file could be copied into the sandbox — every one recorded as a
request and an "allow" decision, about 2 KB each, before every run's first
command. The engine can now authorize without recording what it allows;
what it refuses, asks about, or must audit is recorded as always.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.telemetry import InMemoryTelemetryStore, TelemetryRecorder
from omnicoreagent.governance import (
    AuthorityRequest,
    GovernanceEngine,
    PolicyDeniedError,
    PolicyEffect,
    PolicyRule,
    build_default_policy,
)
from omnicoreagent.governance.hashing import attach_policy_hash


def _engine():
    policy = build_default_policy("interactive-dev")
    policy.rules.deny.insert(
        0,
        PolicyRule(
            rule_id="deny_secrets",
            effect=PolicyEffect.DENY,
            capability="workspace.files.read",
            target={"path": "secret/*"},
        ),
    )
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    return GovernanceEngine(attach_policy_hash(policy), telemetry_recorder=recorder), recorder, store


def _read(path: str) -> AuthorityRequest:
    from omnicoreagent.governance.models import AuthorityTarget

    return AuthorityRequest(
        capability="workspace.files.read", actor="agent", provider="workspace",
        target=AuthorityTarget(path=path, tool_name="read_file"),
    )


@pytest.mark.asyncio
async def test_an_allowed_request_can_go_unrecorded_and_a_refused_one_cannot():
    engine, recorder, store = _engine()
    context = await recorder.start_trace(trace_id="trace-quiet")

    await engine.authorize_all([_read("notes.txt")], record_allows=False)
    with pytest.raises(PolicyDeniedError):
        await engine.authorize_all([_read("secret/key.txt")], record_allows=False)
    await engine.authorize_all([_read("loud.txt")])
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    recorded = [
        (e.event_type, ((e.input or {}).get("request") or {}).get("target", {}).get("path") or e.metadata.get("request_id"))
        for e in trace.events
        if e.event_type.startswith("policy_")
    ]
    paths = [p for _, p in recorded]
    assert not any(p == "notes.txt" for p in paths), recorded
    assert "secret/key.txt" in paths and "loud.txt" in paths, recorded
    assert "policy_decision_deny" in [t for t, _ in recorded]
