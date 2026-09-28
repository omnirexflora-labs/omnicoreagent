"""Memory settings hold (docs pass, 2026-09-27): a partial memory_config
failed with a bare KeyError, and switching the memory store dropped the
window and summary settings."""

from __future__ import annotations

import pytest

from omnicoreagent import OmniCoreAgent
from omnicoreagent.core.memory_store.memory_router import MemoryRouter

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


def test_a_partial_memory_config_keeps_the_other_defaults():
    agent = OmniCoreAgent(
        name="m", system_instruction="x", model_config=MODEL,
        agent_config={"memory_config": {"mode": "token_budget"}},
    )
    memory = agent.agent_config["memory_config"]
    assert memory["mode"] == "token_budget" and memory["value"] == 10000


def test_switching_the_store_keeps_the_memory_settings(tmp_path, monkeypatch):
    pytest.importorskip("sqlalchemy")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'memory.db'}")
    router = MemoryRouter("in_memory")
    router.set_memory_config("sliding_window", 4, {"enabled": False, "retention_policy": "keep"})
    router.switch_memory_store("sql")
    assert router.memory_store.memory_config["value"] == 4
    router.switch_memory_store("in_memory")
    assert router.memory_store.memory_config["value"] == 4
