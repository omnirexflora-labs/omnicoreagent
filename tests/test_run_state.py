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
    # A tool call entry holds a digest, never the arguments (the run's context
    # holds the conversation, stored exactly as the session history stores it).
    assert '"a"' not in str(run["tool_calls"])
    assert all("arguments" not in call for call in run["tool_calls"])
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


@pytest.mark.asyncio
async def test_concurrent_runs_in_one_session_keep_separate_records():
    gate = asyncio.Event()

    class Interleaved(ScriptedModel):
        async def llm_call(self, messages, tools=None, **kwargs):
            await gate.wait()  # both runs are inside their loops before either proceeds
            return await super().llm_call(messages, tools, **kwargs)

    seen: list = []
    first = await _agent(Interleaved([("a1", "lookup", '{"key": "first"}')], "first done"), _tools(seen))
    second = await _agent(Interleaved([("b1", "explode", "{}")], "second done"), _tools(seen))
    second.memory_router = first.memory_router  # one store, one session

    runs = [
        asyncio.create_task(first.run("one", session_id="shared", run_id="run_one")),
        asyncio.create_task(second.run("two", session_id="shared", run_id="run_two")),
    ]
    await asyncio.sleep(0.2)
    gate.set()
    await asyncio.gather(*runs)

    one, two = await first.get_run("run_one"), await first.get_run("run_two")
    assert [c["tool_call_id"] for c in one["tool_calls"]] == ["a1"]
    assert [c["tool_call_id"] for c in two["tool_calls"]] == ["b1"]
    assert one["trace_ids"] != two["trace_ids"]
    assert {r["run_id"] for r in await first.list_runs(session_id="shared")} == {"run_one", "run_two"}


# --- D2a: a run keeps its own working context ---------------------------------


@pytest.mark.asyncio
async def test_a_run_keeps_the_history_it_started_with_and_its_own_messages():
    agent = await _agent(ScriptedModel("first answer", [("k1", "explode", "{}")], "second answer"), _tools([]))

    first = await agent.run("first question", session_id="ctx")
    second = await agent.run("second question", session_id="ctx")
    record = await agent.get_run(second["run_id"])

    history = [(m["role"], m["content"]) for m in record["context"]["history"]]
    own = record["context"]["messages"]
    assert history == [("user", "first question"), ("assistant", "first answer")]
    assert [m["role"] for m in own] == ["user", "assistant", "tool", "assistant"]
    assert own[0]["content"] == "second question"
    assert all(m["metadata"]["run_id"] == second["run_id"] for m in own)
    assert first["run_id"] != second["run_id"]


@pytest.mark.asyncio
async def test_messages_in_the_session_history_carry_their_run():
    agent = await _agent(ScriptedModel("answer"), ToolRegistry())

    result = await agent.run("question", session_id="tagged")
    stored = await agent.memory_router.get_messages("tagged")

    assert {m["metadata"].get("run_id") for m in stored} == {result["run_id"]}


@pytest.mark.asyncio
async def test_other_requests_and_summarization_cannot_change_a_runs_context():
    agent = await _agent(ScriptedModel("a done", *[f"b{i} done" for i in range(6)]), ToolRegistry())
    agent.memory_router.set_memory_config(mode="sliding_window", value=2)

    first = await agent.run("run a", session_id="busy")
    before = await agent.get_run(first["run_id"])
    for i in range(6):  # other requests in the same session push the window on
        await agent.run(f"run b{i}", session_id="busy")
    after = await agent.get_run(first["run_id"])

    assert after["context"] == before["context"]
    assert [m["content"] for m in after["context"]["messages"]] == ["run a", "a done"]


@pytest.mark.asyncio
async def test_a_runs_context_is_stored_as_redacted_as_the_history():
    """When memory must hold no PII at rest, the run's context is redacted
    the same way (and a resumed run sees the redacted text)."""
    from omnicoreagent.core.privacy import PrivacyConfig, PrivacyFilter

    agent = await _agent(ScriptedModel("noted"), ToolRegistry())
    agent.privacy_filter = PrivacyFilter(PrivacyConfig(redact_memory=True))

    result = await agent.run("email me at someone@example.com", session_id="private")
    record = await agent.get_run(result["run_id"])
    stored = await agent.memory_router.get_messages("private")

    assert "someone@example.com" not in str(record)
    assert record["context"]["messages"][0]["content"] == stored[0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", sorted(BACKENDS))
async def test_every_store_deletes_only_finished_runs_started_before_a_time(backend, tmp_path):
    """Run retention (RR1): a store removes finished runs that started before
    the cutoff, in one call, and never a run still waiting or running."""
    from datetime import datetime, timedelta, timezone

    store = BACKENDS[backend](tmp_path)
    tag = os.urandom(4).hex()
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=60)).isoformat()
    new = now.isoformat()
    runs = {
        f"old_done_{tag}": ("completed", old),
        f"old_failed_{tag}": ("failed", old),
        f"old_waiting_{tag}": ("awaiting_approval", old),
        f"new_done_{tag}": ("completed", new),
    }
    for run_id, (status, created) in runs.items():
        await store.save_run_state(
            {**_record(run_id, f"s_{tag}", status), "created_at": created}, expected_version=None
        )

    removed = await store.delete_finished_run_states(
        before=(now - timedelta(days=30)).isoformat(),
        statuses=("completed", "failed", "blocked", "cancelled", "timeout"),
    )

    assert removed == 2
    assert await store.get_run_state(f"old_done_{tag}") is None
    assert await store.get_run_state(f"old_failed_{tag}") is None
    assert await store.get_run_state(f"old_waiting_{tag}") is not None, "still waiting for a person"
    assert await store.get_run_state(f"new_done_{tag}") is not None
    listed = {r["run_id"] for r in await store.list_run_states(session_id=f"s_{tag}")}
    assert listed == {f"old_waiting_{tag}", f"new_done_{tag}"}, "gone from the listings too"
