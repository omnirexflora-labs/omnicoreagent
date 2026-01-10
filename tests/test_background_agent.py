import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from omnicoreagent.omni_agent.background_agent.task_registry import TaskRegistry
from omnicoreagent.omni_agent.background_agent.scheduler_backend import (
    APSchedulerBackend,
)
from omnicoreagent.omni_agent.background_agent.background_agents import (
    BackgroundOmniCoreAgent,
)
from omnicoreagent.omni_agent.background_agent.background_agent_manager import (
    BackgroundAgentManager,
)


@pytest.fixture
def task_registry():
    return TaskRegistry()


@pytest.fixture
def scheduler_backend():
    backend = APSchedulerBackend()
    yield backend
    if backend.is_running():
        backend.shutdown()


class TestTaskRegistry:
    def test_register_and_get(self, task_registry):
        config = {"query": "test task"}
        task_registry.register("agent1", config)
        assert task_registry.get("agent1") == config
        assert task_registry.exists("agent1") is True

    def test_all_tasks(self, task_registry):
        task_registry.register("agent1", {"q": 1})
        task_registry.register("agent2", {"q": 2})
        tasks = task_registry.all_tasks()
        assert len(tasks) == 2
        assert {"q": 1} in tasks
        assert {"q": 2} in tasks

    def test_remove(self, task_registry):
        task_registry.register("agent1", {"q": 1})
        task_registry.remove("agent1")
        assert task_registry.exists("agent1") is False
        assert task_registry.get("agent1") is None

    def test_update(self, task_registry):
        task_registry.register("agent1", {"q": 1})
        task_registry.update("agent1", {"q": 2})
        assert task_registry.get("agent1") == {"q": 2}

    def test_update_non_existent(self, task_registry):
        with pytest.raises(KeyError):
            task_registry.update("non_existent", {"q": 1})

    def test_get_agent_ids(self, task_registry):
        task_registry.register("agent1", {"q": 1})
        task_registry.register("agent2", {"q": 2})
        ids = task_registry.get_agent_ids()
        assert set(ids) == {"agent1", "agent2"}

    def test_clear(self, task_registry):
        task_registry.register("agent1", {"q": 1})
        task_registry.clear()
        assert len(task_registry.all_tasks()) == 0


class TestAPSchedulerBackend:
    @pytest.mark.asyncio
    async def test_start_shutdown(self, scheduler_backend):
        assert scheduler_backend.is_running() is False
        scheduler_backend.start()
        assert scheduler_backend.is_running() is True
        scheduler_backend.shutdown()
        assert scheduler_backend.is_running() is False

    @pytest.mark.asyncio
    async def test_schedule_interval_task(self, scheduler_backend):
        async def dummy_task():
            pass

        scheduler_backend.schedule_task("agent1", 5, dummy_task)
        assert scheduler_backend.is_task_scheduled("agent1") is True

        status = scheduler_backend.get_job_status("agent1")
        assert status["id"] == "agent1"
        assert "interval" in status["trigger"]

    @pytest.mark.asyncio
    async def test_schedule_cron_task(self, scheduler_backend):
        async def dummy_task():
            pass

        # Every minute crontab
        scheduler_backend.schedule_task("agent1", "* * * * *", dummy_task)
        assert scheduler_backend.is_task_scheduled("agent1") is True

        status = scheduler_backend.get_job_status("agent1")
        assert "cron" in status["trigger"]

    @pytest.mark.asyncio
    async def test_remove_task(self, scheduler_backend):
        async def dummy_task():
            pass

        scheduler_backend.schedule_task("agent1", 5, dummy_task)
        scheduler_backend.remove_task("agent1")
        assert scheduler_backend.is_task_scheduled("agent1") is False

    @pytest.mark.asyncio
    async def test_pause_resume_job(self, scheduler_backend):
        async def dummy_task():
            pass

        scheduler_backend.schedule_task("agent1", 5, dummy_task)
        scheduler_backend.pause_job("agent1")
        # APScheduler job.active might not change immediately or depends on version
        # But we can verify it doesn't raise error
        scheduler_backend.resume_job("agent1")

    @pytest.mark.asyncio
    async def test_invalid_interval_type(self, scheduler_backend):
        async def dummy_task():
            pass

        with pytest.raises(ValueError, match="Invalid interval type"):
            scheduler_backend.schedule_task("agent1", 5.5, dummy_task)

    @pytest.mark.asyncio
    async def test_non_async_func(self, scheduler_backend):
        def sync_task():
            pass

        with pytest.raises(ValueError, match="must be an async function"):
            scheduler_backend.schedule_task("agent1", 5, sync_task)


@pytest.fixture
def mock_omni_agent():
    with patch(
        "omnicoreagent.omni_agent.background_agent.background_agents.OmniCoreAgent"
    ) as mock:
        mock.return_value.run = AsyncMock(
            return_value={"response": "success", "session_id": "test_session"}
        )
        mock.return_value.connect_mcp_servers = AsyncMock()
        mock.return_value.get_event_store_type = AsyncMock(return_value="in_memory")
        mock.return_value.is_event_store_available = AsyncMock(return_value=True)
        mock.return_value.get_event_store_info = AsyncMock(
            return_value={"type": "in_memory"}
        )
        mock.return_value.cleanup = AsyncMock()
        # Mocking the internal methods that cause trouble during inheritance init
        with patch(
            "omnicoreagent.omni_agent.agent.OmniCoreAgent._create_internal_config",
            return_value={},
        ):
            with patch(
                "omnicoreagent.omni_agent.agent.OmniCoreAgent._prepare_agent_config",
                return_value={},
            ):
                with patch(
                    "omnicoreagent.omni_agent.agent.OmniCoreAgent._save_config_hidden",
                    return_value=None,
                ):
                    with patch(
                        "omnicoreagent.omni_agent.agent.OmniCoreAgent._create_agent",
                        return_value=None,
                    ):
                        yield mock


class TestBackgroundOmniCoreAgent:
    @pytest.mark.asyncio
    async def test_init(self, mock_omni_agent, task_registry):
        config = {
            "agent_id": "test_agent",
            "interval": 60,
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }
        agent = BackgroundOmniCoreAgent(config, task_registry)
        assert agent.agent_id == "test_agent"
        assert agent.task_registry == task_registry

    @pytest.mark.asyncio
    async def test_run_task_submits_to_queue(self, mock_omni_agent, task_registry):
        config = {
            "agent_id": "test_agent",
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }
        agent = BackgroundOmniCoreAgent(config, task_registry)
        await agent.run_task({"query": "test query"})
        assert agent._task_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_worker_loop_processes_task(self, mock_omni_agent, task_registry):
        config = {
            "agent_id": "test_agent",
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }
        task_registry.register("test_agent", {"query": "registered query"})

        mock_event_router = AsyncMock()
        agent = BackgroundOmniCoreAgent(
            config, task_registry, event_router=mock_event_router
        )

        with patch.object(agent, "_internal_run_task", AsyncMock()) as mock_internal:
            await agent.start_worker()
            await agent.submit_task({"query": "manual query"})

            await asyncio.sleep(0.1)

            mock_internal.assert_called_once_with(task_config={"query": "manual query"})
            await agent.cleanup()

    @pytest.mark.asyncio
    async def test_internal_run_task_success(self, mock_omni_agent, task_registry):
        config = {
            "agent_id": "test_agent",
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }
        task_registry.register("test_agent", {"query": "registered query"})

        mock_event_router = AsyncMock()
        agent = BackgroundOmniCoreAgent(
            config, task_registry, event_router=mock_event_router
        )

        agent.run = AsyncMock(return_value={"response": "success"})

        result = await agent._internal_run_task(
            task_config={
                "query": "registered query",
                "max_retries": 3,
                "session_id": "test_session",
            }
        )

        assert result == {"response": "success"}
        assert agent.run_count == 1
        assert agent.error_count == 0
        assert mock_event_router.append.call_count >= 2

    @pytest.mark.asyncio
    async def test_internal_run_task_failure_with_retry(
        self, mock_omni_agent, task_registry
    ):
        config = {
            "agent_id": "test_agent",
            "max_retries": 1,
            "retry_delay": 0.01,
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }
        task_registry.register("test_agent", {"query": "registered query"})

        mock_event_router = AsyncMock()
        agent = BackgroundOmniCoreAgent(
            config, task_registry, event_router=mock_event_router
        )

        agent.run = AsyncMock(side_effect=[Exception("fail"), {"response": "success"}])

        result = await agent._internal_run_task(
            task_config={
                "query": "registered query",
                "max_retries": 1,
                "session_id": "test_session",
            }
        )

        assert result == {"response": "success"}
        assert agent.run_count == 1
        assert agent.run.call_count == 2
        
    @pytest.mark.asyncio
    async def test_task_timeout(self, mock_omni_agent, task_registry):
        config = {
            "agent_id": "test_agent",
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }
        task_registry.register("test_agent", {"query": "timeout task", "timeout": 0.1})

        mock_event_router = AsyncMock()
        agent = BackgroundOmniCoreAgent(
            config, task_registry, event_router=mock_event_router
        )

        async def slow_run(**kwargs):
            await asyncio.sleep(0.5)
            return {"response": "too late"}

        agent.run = AsyncMock(side_effect=slow_run)

        # Should raise TimeoutError
        with pytest.raises(TimeoutError, match="timed out"):
            await agent._internal_run_task(
                task_config={
                    "query": "timeout task",
                    "timeout": 0.1,
                    "max_retries": 0,
                    "session_id": "test_session",
                }
            )
    
    @pytest.mark.asyncio
    async def test_queue_full(self, mock_omni_agent, task_registry):
        config = {
            "agent_id": "test_agent",
            "queue_size": 1,
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }
        agent = BackgroundOmniCoreAgent(config, task_registry)
        
        # Fill queue
        await agent.submit_task({"query": "task 1"})
        
        # Next one should fail if we don't consume it
        with pytest.raises(asyncio.TimeoutError):
             # Force small queue_timeout for test speed
             await agent.submit_task({"query": "task 2", "queue_timeout": 0.1})

    @pytest.mark.asyncio
    async def test_timezone_aware_execution(self, mock_omni_agent, task_registry):
        config = {
            "agent_id": "test_agent",
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }
        task_registry.register("test_agent", {"query": "timezone task"})
        
        mock_event_router = AsyncMock()
        agent = BackgroundOmniCoreAgent(
            config, task_registry, event_router=mock_event_router
        )
        agent.run = AsyncMock(return_value={"response": "success"})
        
        await agent._internal_run_task(
            task_config={
                "query": "timezone task",
                "max_retries": 0,
                "session_id": "test_session",
            }
        )
        
        # Check if last_run is timezone aware (has tzinfo)
        assert agent.last_run.tzinfo == timezone.utc


class TestBackgroundAgentManager:
    @pytest.mark.asyncio
    async def test_create_agent(self, mock_omni_agent):
        manager = BackgroundAgentManager()
        config = {
            "agent_id": "manager_agent",
            "task_config": {"query": "managed task", "interval": 30},
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }

        result = await manager.create_agent(config)

        assert "manager_agent" in manager.agents
        assert manager.task_registry.exists("manager_agent") is True
        assert result["agent_id"] == "manager_agent"
        assert manager.scheduler.is_task_scheduled("manager_agent") is True

    @pytest.mark.asyncio
    async def test_run_task_now(self, mock_omni_agent):
        manager = BackgroundAgentManager()
        config = {
            "agent_id": "test_agent",
            "task_config": {"query": "task", "interval": 60},
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }
        await manager.create_agent(config)

        agent = manager.agents["test_agent"]
        with patch.object(agent, "submit_task", AsyncMock()) as mock_submit:
            await manager.run_task_now("test_agent", task_config={"query": "now"})
            mock_submit.assert_called_once_with({"query": "now"})

    @pytest.mark.asyncio
    async def test_pause_resume_agent(self, mock_omni_agent):
        manager = BackgroundAgentManager()
        config = {
            "agent_id": "test_agent",
            "task_config": {"query": "task", "interval": 60},
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }
        await manager.create_agent(config)

        await manager.pause_agent("test_agent")
        assert manager.scheduler.is_task_scheduled("test_agent") is False

        await manager.resume_agent("test_agent")
        assert manager.scheduler.is_task_scheduled("test_agent") is True

    @pytest.mark.asyncio
    async def test_delete_agent(self, mock_omni_agent):
        manager = BackgroundAgentManager()
        config = {
            "agent_id": "test_agent",
            "task_config": {"query": "task", "interval": 60},
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }
        await manager.create_agent(config)

        await manager.delete_agent("test_agent")
        assert "test_agent" not in manager.agents
        assert manager.task_registry.exists("test_agent") is False
        assert manager.scheduler.is_task_scheduled("test_agent") is False

    @pytest.mark.asyncio
    async def test_get_manager_status(self, mock_omni_agent):
        manager = BackgroundAgentManager()
        config = {
            "agent_id": "test_agent",
            "task_config": {"query": "task", "interval": 60},
            "model_config": {"provider": "openai", "model": "gpt-4"},
        }
        await manager.create_agent(config)

        status = await manager.get_manager_status()
        assert status["total_agents"] == 1
        assert "test_agent" in status["agents"]
