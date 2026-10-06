"""Two agents on one telemetry store must agree on `retention_days`.

0.5.1, B8: the store is one object per file in a process, built by the first
agent; the second agent's `retention_days` was only logged and then ignored,
so it silently got the first one's. A second, different value is now refused
when the second agent opens the store (`initialize()`, where the store is
bound), naming both; the same value is fine.
"""

from __future__ import annotations

import pytest

from omnicoreagent import OmniCoreAgent
from omnicoreagent.core.telemetry import store as store_module

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


def _agent(tmp_path, name, retention_days):
    return OmniCoreAgent(
        name=name,
        system_instruction="x",
        model_config=MODEL,
        agent_config={"guardrail_mode": "off"},
        telemetry_config={
            "storage_path": str(tmp_path / "traces.jsonl"),
            "retention_days": retention_days,
        },
    )


@pytest.mark.asyncio
async def test_the_same_retention_on_one_store_is_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_SHARED_JSONL_STORES", store_module.weakref.WeakValueDictionary())
    first, second = _agent(tmp_path, "first", 14), _agent(tmp_path, "second", 14)
    await first.initialize()
    await second.initialize()
    assert first.telemetry_store is second.telemetry_store
    assert first.telemetry_store.retention_days == 14


@pytest.mark.asyncio
async def test_a_different_retention_on_one_store_is_refused_naming_both(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_SHARED_JSONL_STORES", store_module.weakref.WeakValueDictionary())
    first = _agent(tmp_path, "first", 14)
    await first.initialize()
    with pytest.raises(ValueError, match=r"retention_days=30.*retention_days=14|14.*30") as refused:
        await _agent(tmp_path, "second", 30).initialize()
    assert "traces.jsonl" in str(refused.value)
    assert first.telemetry_store.retention_days == 14, "the first agent's window is untouched"


@pytest.mark.asyncio
async def test_none_is_a_value_too(tmp_path, monkeypatch):
    # `None` keeps every trace; an agent that keeps them all must not have the
    # first one's seven days.
    monkeypatch.setattr(store_module, "_SHARED_JSONL_STORES", store_module.weakref.WeakValueDictionary())
    first = _agent(tmp_path, "first", 7)
    await first.initialize()
    with pytest.raises(ValueError, match="retention_days"):
        await _agent(tmp_path, "second", None).initialize()


def test_asking_the_shared_store_for_nothing_in_particular_takes_what_it_has(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_SHARED_JSONL_STORES", store_module.weakref.WeakValueDictionary())
    path = tmp_path / "traces.jsonl"
    first = store_module.shared_jsonl_telemetry_store(path, retention_days=14)
    assert store_module.shared_jsonl_telemetry_store(path) is first
    with pytest.raises(ValueError, match="14"):
        store_module.shared_jsonl_telemetry_store(path, retention_days=3)
