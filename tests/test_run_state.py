"""Durable runs, D1: every run's state is saved in the chosen memory store.

The run state records what session history does not: status, step, usage,
the trace, and the state of each tool call (written before the tool starts and
after it ends). The store contract runs against every built-in backend; Redis
and MongoDB run when their URLs are configured.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from omnicoreagent.core.memory_store.in_memory import InMemoryStore
from omnicoreagent.core.runs import RunStateConflict
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from test_execute_tool import ScriptedModel, _MODEL


def _sql_store(tmp_path):
    pytest.importorskip("sqlalchemy")
    from omnicoreagent.core.memory_store.sql_db_memory import DatabaseMessageStore

    return DatabaseMessageStore(db_url=f"sqlite:///{tmp_path / 'memory.db'}")


def _redis_store(tmp_path):
    url = os.environ.get("OMNICOREAGENT_TEST_REDIS_URL")
    if not url:
        pytest.skip("Redis run state requires OMNICOREAGENT_TEST_REDIS_URL")
    from omnicoreagent.core.memory_store.redis_memory import RedisMemoryStore

    return RedisMemoryStore(redis_url=url)


def _mongo_store(tmp_path):
    uri = os.environ.get("OMNICOREAGENT_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("MongoDB run state requires OMNICOREAGENT_TEST_MONGODB_URI")
    from omnicoreagent.core.memory_store.mongodb import MongoDb

    return MongoDb(uri=uri, db_name="omnicoreagent_test", collection="messages")


BACKENDS = {
    "in_memory": lambda tmp_path: InMemoryStore(),
    "sql": _sql_store,
    "redis": _redis_store,
    "mongodb": _mongo_store,
}


def _record(run_id, session_id="s1", status="running"):
    return {"run_id": run_id, "session_id": session_id, "status": status, "step": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", sorted(BACKENDS))
async def test_every_memory_store_keeps_run_state_with_versions(backend, tmp_path):
    store = BACKENDS[backend](tmp_path)
    run_id = f"run_{backend}_{os.urandom(4).hex()}"

    assert await store.save_run_state(_record(run_id), expected_version=None) == 1
    with pytest.raises(RunStateConflict):
        await store.save_run_state(_record(run_id), expected_version=None)

    updated = {**_record(run_id, status="completed"), "step": 3}
    assert await store.save_run_state(updated, expected_version=1) == 2
    with pytest.raises(RunStateConflict):
        await store.save_run_state(updated, expected_version=1)

    loaded = await store.get_run_state(run_id)
    assert loaded["status"] == "completed" and loaded["step"] == 3 and loaded["version"] == 2
    assert await store.get_run_state("run_missing") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", sorted(BACKENDS))
async def test_runs_are_listed_by_session_and_status(backend, tmp_path):
    store = BACKENDS[backend](tmp_path)
    session = f"s_{os.urandom(4).hex()}"
    await store.save_run_state(_record(f"{session}_a", session, "completed"), expected_version=None)
    await store.save_run_state(_record(f"{session}_b", session, "running"), expected_version=None)
    await store.save_run_state(_record(f"{session}_c", "other", "running"), expected_version=None)

    in_session = {r["run_id"] for r in await store.list_run_states(session_id=session)}
    running = {r["run_id"] for r in await store.list_run_states(session_id=session, status="running")}

    assert in_session == {f"{session}_a", f"{session}_b"}
    assert running == {f"{session}_b"}


# --- the agent saves its runs ------------------------------------------------


def _tools(seen: list):
    tools = ToolRegistry()

    @tools.register_tool("lookup", description="Looks up a value.")
    async def lookup(key: str) -> dict:
        from omnicoreagent.core.runs import current_run

        # The call must already be recorded as started before the tool runs.
        record = await current_run().load()
        seen.append([(c["tool_name"], c["state"]) for c in record["tool_calls"]])
        return {"status": "success", "data": {"key": key, "value": 42}}

    @tools.register_tool("explode", description="Always fails.")
    def explode() -> dict:
        raise RuntimeError("tool failed")

    return tools


async def _agent(model, tools, **config):
    agent = OmniCoreAgent(
        name="durable",
        system_instruction="Use tools.",
        model_config=_MODEL,
        local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, **config},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


@pytest.mark.asyncio
async def test_a_completed_run_is_saved_with_its_steps_tool_calls_and_usage():
    seen: list = []
    model = ScriptedModel(
        [("c1", "lookup", '{"key": "a"}'), ("c2", "explode", "{}")],
        "done",
    )
    agent = await _agent(model, _tools(seen))

    result = await agent.run("go", session_id="durable-1")
    run = await agent.get_run(result["run_id"])

    assert run["status"] == "completed"
    assert run["session_id"] == "durable-1" and run["agent_name"] == "durable"
    assert run["step"] == 2
    assert run["trace_ids"] == [result["trace_id"]]
    calls = {c["tool_call_id"]: c for c in run["tool_calls"]}
    assert (calls["c1"]["state"], calls["c1"]["outcome"]) == ("completed", "success")
    assert (calls["c2"]["state"], calls["c2"]["outcome"]) == ("completed", "error")
    assert len(calls["c1"]["arguments_digest"]) == 64
    assert '"a"' not in str(run), "arguments are recorded as a digest, not in full"
    assert run["usage"]["requests"] == 2
    assert ("lookup", "started") in seen[0]


@pytest.mark.asyncio
async def test_runs_are_listed_for_a_session():
    agent = await _agent(ScriptedModel("one", "two"), ToolRegistry())

    first = await agent.run("a", session_id="durable-2")
    second = await agent.run("b", session_id="durable-2")

    listed = [r["run_id"] for r in await agent.list_runs(session_id="durable-2")]
    assert sorted(listed) == sorted([first["run_id"], second["run_id"]])


@pytest.mark.asyncio
async def test_a_failed_run_is_saved_as_failed():
    class Broken(ScriptedModel):
        async def llm_call(self, messages, tools=None, **kwargs):
            raise RuntimeError("provider down")

    agent = await _agent(Broken(), ToolRegistry())

    # A provider failure ends the run with an error response, not an exception.
    result = await agent.run("go", session_id="durable-3", run_id="run_failing")
    run = await agent.get_run("run_failing")

    assert result.get("status") == "error"
    assert run["status"] == "failed"


@pytest.mark.asyncio
async def test_a_cancelled_run_is_saved_as_cancelled():
    started = asyncio.Event()

    class Slow(ScriptedModel):
        async def llm_call(self, messages, tools=None, **kwargs):
            started.set()
            await asyncio.sleep(30)

    agent = await _agent(Slow(), ToolRegistry())
    task = asyncio.create_task(agent.run("go", session_id="durable-4", run_id="run_cancelled"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run = await agent.get_run("run_cancelled")
    assert run["status"] == "cancelled"


@pytest.mark.asyncio
async def test_a_custom_memory_store_without_run_state_still_runs():
    from omnicoreagent.core.memory_store.base import AbstractMemoryStore

    class Minimal(AbstractMemoryStore):
        def __init__(self):
            self.messages = []

        def set_memory_config(self, mode, value=None, summary_config=None, summarize_fn=None):
            pass

        async def store_message(self, role, content, metadata, session_id):
            self.messages.append({"role": role, "content": content, "msg_metadata": metadata})

        async def get_messages(self, session_id=None, agent_name=None):
            return [dict(m) for m in self.messages]

        async def clear_memory(self, session_id=None, agent_name=None):
            self.messages.clear()

        async def mark_messages_summarized(self, message_ids, summary_id, retention_policy="keep"):
            pass

    agent = await _agent(ScriptedModel("fine"), ToolRegistry())
    agent.memory_router.memory_store = Minimal()

    result = await agent.run("go", session_id="durable-5")

    assert result["response"] == "fine"
    assert await agent.get_run(result["run_id"]) is None


def test_a_configured_but_unreachable_durable_store_is_a_warning(monkeypatch, caplog):
    import logging

    from omnicoreagent.core.memory_store.memory_router import MemoryRouter

    monkeypatch.delenv("REDIS_URL", raising=False)
    with caplog.at_level(logging.WARNING):
        MemoryRouter("redis")

    assert "REDIS_URL" in caplog.text and "in_memory" in caplog.text


# --- stores with different addresses stay separate --------------------------


@pytest.mark.asyncio
async def test_two_sql_stores_with_different_databases_keep_separate_data(tmp_path):
    pytest.importorskip("sqlalchemy")
    from omnicoreagent.core.memory_store.sql_db_memory import DatabaseMessageStore

    first = DatabaseMessageStore(db_url=f"sqlite:///{tmp_path / 'one.db'}")
    second = DatabaseMessageStore(db_url=f"sqlite:///{tmp_path / 'two.db'}")
    await first.store_message("user", "only in one", {"agent_name": "a"}, "s")

    assert [m["content"] for m in await first.get_messages("s")] == ["only in one"]
    assert await second.get_messages("s") == []


@pytest.mark.asyncio
async def test_two_redis_stores_with_different_databases_keep_separate_data():
    url = os.environ.get("OMNICOREAGENT_TEST_REDIS_URL")
    if not url:
        pytest.skip("requires OMNICOREAGENT_TEST_REDIS_URL")
    from omnicoreagent.core.memory_store.redis_memory import RedisMemoryStore

    base = url.rsplit("/", 1)[0] if url.count("/") > 2 else url
    first, second = RedisMemoryStore(redis_url=f"{base}/1"), RedisMemoryStore(redis_url=f"{base}/2")
    session = f"sep_{os.urandom(4).hex()}"
    await first.store_message("user", "only in one", {"agent_name": "a"}, session)

    assert [m["content"] for m in await first.get_messages(session)] == ["only in one"]
    assert await second.get_messages(session) == []


def _router_env(backend, tmp_path, monkeypatch):
    if backend == "sql":
        pytest.importorskip("sqlalchemy")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'agent.db'}")
    elif backend == "redis":
        url = os.environ.get("OMNICOREAGENT_TEST_REDIS_URL")
        if not url:
            pytest.skip("requires OMNICOREAGENT_TEST_REDIS_URL")
        monkeypatch.setenv("REDIS_URL", url)
    else:
        uri = os.environ.get("OMNICOREAGENT_TEST_MONGODB_URI")
        if not uri:
            pytest.skip("requires OMNICOREAGENT_TEST_MONGODB_URI")
        monkeypatch.setenv("MONGODB_URI", uri)
        monkeypatch.setenv("MONGODB_DB_NAME", "omnicoreagent_test")


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sql", "redis", "mongodb"])
async def test_a_run_record_outlives_the_agent_that_made_it(backend, tmp_path, monkeypatch):
    from omnicoreagent.core.memory_store.memory_router import MemoryRouter

    _router_env(backend, tmp_path, monkeypatch)
    seen: list = []
    first = OmniCoreAgent(
        name="durable",
        system_instruction="Use tools.",
        model_config=_MODEL,
        local_tools=_tools(seen),
        memory_router=MemoryRouter(backend),
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    await first.initialize()
    first.llm_connection = ScriptedModel([("c1", "lookup", '{"key": "a"}')], "done")
    session = f"outlive_{os.urandom(4).hex()}"
    result = await first.run("go", session_id=session)

    # A separate agent with its own connection, as after a restart.
    second = OmniCoreAgent(
        name="durable",
        system_instruction="Use tools.",
        model_config=_MODEL,
        memory_router=MemoryRouter(backend),
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    run = await second.get_run(result["run_id"])

    assert run["status"] == "completed" and run["step"] == 2
    assert [(c["tool_name"], c["state"]) for c in run["tool_calls"]] == [("lookup", "completed")]
    assert [r["run_id"] for r in await second.list_runs(session_id=session)] == [result["run_id"]]
