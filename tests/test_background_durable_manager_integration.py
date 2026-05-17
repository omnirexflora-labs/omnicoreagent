from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from omnicoreagent.background import (
    BackgroundAgentManager,
    MongoDbTaskStore,
    RedisTaskStore,
    RunStatus,
)
from omnicoreagent.core.workspace.manager import Workspace


DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_MONGODB_URI = "mongodb://localhost:27017"
DEFAULT_MONGODB_DATABASE = "omnicoreagent_test"

pytestmark = pytest.mark.requires_network


class RestartAgent:
    async def run(self, query: str, session_id: str | None = None):
        return {
            "response": (
                "restored background run complete; "
                f"session={session_id}; "
                f"workspace_guidance={'/workspace/background/' in query}"
            )
        }


async def run_restart_integration(
    task_store_config: dict,
    workspace_dir: Path,
) -> None:
    workspace = Workspace.from_config(workspace_dir=workspace_dir).ensure()

    first = BackgroundAgentManager(
        task_store=task_store_config,
        workspace=workspace,
        worker_id="before_restart",
    )
    await first.register_agent("agent", RestartAgent())
    task = await first.register_task(
        task_id="restart_task",
        agent_id="agent",
        query="Run after the manager restarts.",
        schedule={"type": "manual"},
        retry_policy={"max_retries": 1, "initial_delay_seconds": 0},
    )
    queued = await first.run_now("restart_task")
    await first.shutdown()

    restored = BackgroundAgentManager(
        task_store=task_store_config,
        workspace=workspace,
        worker_id="after_restart",
    )
    try:
        await restored.initialize()
        await restored.register_agent("agent", RestartAgent(), replace=True)
        completed = await restored.run_until_terminal(queued.run_id, timeout_seconds=15)

        assert completed.status == RunStatus.COMPLETED
        assert completed.query_snapshot == "Run after the manager restarts."
        assert completed.result_preview is not None
        assert f"session=background:{task.agent_id}:{task.task_id}" in (
            completed.result_preview
        )
        assert "workspace_guidance=True" in completed.result_preview
        assert completed.lease_owner is None
        assert completed.lease_token is None

        task_status = await restored.get_task_status("restart_task")
        manager_status = await restored.get_manager_status()
        events = await restored.get_run_events(queued.run_id)
        workspace_state = await restored.get_run_workspace(queued.run_id)

        assert task_status["runs"] == 1
        assert task_status["active_runs"] == 0
        assert task_status["latest_run"]["run_id"] == queued.run_id
        assert task_status["status_counts"]["completed"] == 1
        assert manager_status["runs"] == 1
        assert manager_status["status_counts"]["completed"] == 1
        assert [event["sequence"] for event in events] == list(
            range(1, len(events) + 1)
        )
        assert events[0]["event"] == "background_run_queued"
        assert events[-1]["event"] == "background_run_completed"
        assert {"run.json", "events.jsonl"}.issubset(
            {item["name"] for item in workspace_state["files"]}
        )
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
async def test_redis_manager_executes_queued_run_after_restart(tmp_path):
    prefix = f"test:omnicoreagent:background:manager:{uuid4().hex}"
    config = {
        "backend": "redis",
        "url": os.getenv("OMNICOREAGENT_TEST_REDIS_URL", DEFAULT_REDIS_URL),
        "prefix": prefix,
        "connect_timeout": 0.2,
    }
    await require_redis_available(config)
    try:
        await run_restart_integration(config, tmp_path / "redis_workspace")
    finally:
        await cleanup_redis_prefix(config)


@pytest.mark.asyncio
async def test_mongodb_manager_executes_queued_run_after_restart(tmp_path):
    collection_prefix = f"test_omnicoreagent_background_manager_{uuid4().hex}"
    config = {
        "backend": "mongodb",
        "uri": os.getenv("OMNICOREAGENT_TEST_MONGODB_URI", DEFAULT_MONGODB_URI),
        "database": os.getenv(
            "OMNICOREAGENT_TEST_MONGODB_DATABASE",
            DEFAULT_MONGODB_DATABASE,
        ),
        "collection_prefix": collection_prefix,
        "connect_timeout": 0.2,
    }
    await require_mongodb_available(config)
    try:
        await run_restart_integration(config, tmp_path / "mongodb_workspace")
    finally:
        await cleanup_mongodb_prefix(config)


async def require_redis_available(config: dict) -> None:
    try:
        from redis.asyncio import Redis
    except Exception as exc:
        skip_or_fail_unavailable(redis_unavailable_message(config, exc))

    client = Redis.from_url(
        config["url"],
        decode_responses=True,
        socket_connect_timeout=config["connect_timeout"],
    )
    try:
        await client.ping()
    except Exception as exc:
        skip_or_fail_unavailable(redis_unavailable_message(config, exc))
    finally:
        await client.aclose()


async def require_mongodb_available(config: dict) -> None:
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except Exception as exc:
        skip_or_fail_unavailable(mongodb_unavailable_message(config, exc))

    client = AsyncIOMotorClient(
        config["uri"],
        serverSelectionTimeoutMS=int(config["connect_timeout"] * 1000),
    )
    try:
        await client[config["database"]].command("ping")
    except Exception as exc:
        skip_or_fail_unavailable(mongodb_unavailable_message(config, exc))
    finally:
        client.close()


async def cleanup_redis_prefix(config: dict) -> None:
    store = RedisTaskStore(
        url=config["url"],
        prefix=config["prefix"],
        connect_timeout=config["connect_timeout"],
    )
    await store.initialize()
    try:
        client = store._require_client()
        keys = [key async for key in client.scan_iter(f"{store.prefix}:*")]
        if keys:
            await client.delete(*keys)
    finally:
        await store.close()


async def cleanup_mongodb_prefix(config: dict) -> None:
    store = MongoDbTaskStore(
        uri=config["uri"],
        database=config["database"],
        collection_prefix=config["collection_prefix"],
        connect_timeout=config["connect_timeout"],
    )
    await store.initialize()
    try:
        db = store._db
        if db is None:
            raise AssertionError("MongoDB cleanup store did not initialize database")
        collection_names = await db.list_collection_names()
        for name in collection_names:
            if name.startswith(f"{store.collection_prefix}_"):
                await db.drop_collection(name)
    finally:
        await store.close()


def _summarize_exception(exc: Exception) -> str:
    message = str(exc).splitlines()[0]
    message = message.split(", Topology Description:", 1)[0]
    return f"{type(exc).__name__}: {message[:240]}"


def skip_or_fail_unavailable(message: str) -> None:
    if os.getenv("CI", "").lower() == "true":
        raise AssertionError(message)
    pytest.skip(message)


def redis_unavailable_message(config: dict, exc: Exception) -> str:
    return (
        "Redis task store unavailable. To run live Redis manager integration "
        f"tests, set OMNICOREAGENT_TEST_REDIS_URL or start Redis at "
        f"{DEFAULT_REDIS_URL}. Configured URL: {config['url']}. "
        f"Error: {_summarize_exception(exc)}"
    )


def mongodb_unavailable_message(config: dict, exc: Exception) -> str:
    return (
        "MongoDB task store unavailable. To run live MongoDB manager integration "
        "tests, set OMNICOREAGENT_TEST_MONGODB_URI and "
        "OMNICOREAGENT_TEST_MONGODB_DATABASE, or start MongoDB at "
        f"{DEFAULT_MONGODB_URI}. Configured URI: {config['uri']}; "
        f"database: {config['database']}. Error: {_summarize_exception(exc)}"
    )
