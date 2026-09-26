"""A sandbox's network isolation check is in the record (stranger test,
round two): E2B and Daytona check it and keep the result on the session,
but the `sandbox_session_created` event never carried it."""

from __future__ import annotations

from types import SimpleNamespace

from omnicoreagent.sandbox.execution import _session_facts


def test_the_isolation_check_is_recorded_with_the_session():
    session = SimpleNamespace(
        session_id="s1", provider="e2b", metadata={"sandbox_id": "sb_1", "network_isolation": "checked"}
    )
    runtime = SimpleNamespace(provider="e2b")
    assert _session_facts(session, runtime)["network_isolation"] == "checked"


def test_a_provider_that_does_not_check_records_nothing_it_did_not_do():
    session = SimpleNamespace(session_id="s2", provider="docker", metadata={"container_id": "c1"})
    runtime = SimpleNamespace(provider="docker")
    assert "network_isolation" not in _session_facts(session, runtime)
