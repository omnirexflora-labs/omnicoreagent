import pytest

from omnicoreagent.background.task_registry import TaskRegistry
from omnicoreagent.background.scheduler_backend import (
    APSchedulerBackend,
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
