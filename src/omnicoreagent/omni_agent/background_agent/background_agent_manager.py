"""
Background Agent Manager for orchestrating multiple background agents.
"""

import asyncio

from typing import Any, Dict, List, Optional
from datetime import datetime

from omnicoreagent.omni_agent.background_agent.background_agents import (
    BackgroundOmniCoreAgent,
)
from omnicoreagent.omni_agent.background_agent.task_registry import TaskRegistry
from omnicoreagent.omni_agent.background_agent.scheduler_backend import (
    APSchedulerBackend,
)
from omnicoreagent.core.memory_store.memory_router import MemoryRouter
from omnicoreagent.core.events.event_router import EventRouter
from omnicoreagent.core.utils import logger
from omnicoreagent.omni_agent.background_agent.task_registry import TaskConfig


class BackgroundAgentManager:
    """Manager for orchestrating multiple background agents."""

    def __init__(
        self,
        memory_router: Optional[MemoryRouter] = None,
        event_router: Optional[EventRouter] = None,
    ):
        """
        Initialize BackgroundAgentManager.

        Args:
            memory_router: Optional shared memory router for all agents
            event_router: Optional shared event router for all agents
        """
        self.memory_router = memory_router or MemoryRouter(
            memory_store_type="in_memory"
        )
        self.event_router = event_router or EventRouter(event_store_type="in_memory")

        self.task_registry = TaskRegistry()
        self.scheduler = APSchedulerBackend()

        self.agents: Dict[str, BackgroundOmniCoreAgent] = {}
        self.agent_configs: Dict[str, Dict[str, Any]] = {}

        self.is_running = False
        self.created_at = datetime.now()

        logger.info("Initialized BackgroundAgentManager")

    async def create_agent(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new background agent.

        Args:
            config: Agent configuration dictionary (task_config will be moved to TaskRegistry)

        Returns:
            Dict containing agent_id, session_id, and event streaming information
        """
        try:
            agent_id = config.get("agent_id")
            if not agent_id:
                raise ValueError("agent_id is required in config")

            if agent_id in self.agents:
                raise ValueError(f"Agent with ID {agent_id} already exists")

            task_config = config.pop("task_config", None)
            if not task_config:
                raise ValueError(f"task_config is required for agent {agent_id}")

            self.task_registry.register(agent_id, task_config)
            logger.info(f"Registered task in TaskRegistry for agent {agent_id}")

            agent = BackgroundOmniCoreAgent(
                config=config,
                memory_router=self.memory_router,
                event_router=self.event_router,
                task_registry=self.task_registry,
            )
            mcp_tools = config.get("mcp_tools", [])
            if mcp_tools:
                await agent.connect_mcp_servers()

            self.agents[agent_id] = agent
            self.agent_configs[agent_id] = config.copy()

            if not self.is_running:
                logger.info(
                    "Auto-starting BackgroundAgentManager for immediate scheduling"
                )
                await self.start()
            else:
                # If manager is already running, start the worker for this new agent
                await agent.start_worker()

            await self._schedule_agent(agent_id, agent)

            event_stream_info = await agent.get_event_stream_info()

            logger.info(f"Created background agent: {agent_id}")

            return {
                "agent_id": agent_id,
                "session_id": await agent.get_session_id(),
                "event_stream_info": event_stream_info,
                "task_registered": True,
                "task_query": await agent.get_task_query(),
                "status": "created_and_scheduled",
                "message": f"Agent {agent_id} created and scheduled successfully. Use session_id '{await agent.get_session_id()}' for event streaming.",
            }

        except Exception as e:
            logger.error(f"Failed to create agent: {e}")
            raise

    async def register_task(self, agent_id: str, task_config: Dict[str, Any]) -> bool:
        """
        Register a task for an existing agent.

        Args:
            agent_id: The agent ID
            task_config: Task configuration dictionary

        Returns:
            True if task was registered successfully
        """
        try:
            self.task_registry.register(agent_id, task_config)
            logger.info(f"Registered/Updated task for agent {agent_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to register task for agent {agent_id}: {e}")
            return False

    async def get_task_config(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get task configuration for an agent."""
        return self.task_registry.get(agent_id)

    async def update_task_config(
        self, agent_id: str, task_config: Dict[str, Any]
    ) -> bool:
        """Update task configuration for an agent."""
        try:
            self.task_registry.update(agent_id, task_config)
            logger.info(f"Updated task configuration for agent {agent_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update task for agent {agent_id}: {e}")
            return False

    async def remove_task(self, agent_id: str) -> bool:
        """Remove task configuration for an agent."""
        try:
            self.task_registry.remove(agent_id)
            logger.info(f"Removed task for agent {agent_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to remove task for agent {agent_id}: {e}")
            return False

    async def list_tasks(self) -> List[str]:
        """List all registered task agent IDs."""
        return self.task_registry.get_agent_ids()

    async def run_task_now(self, agent_id: str, task_config: Dict[str, Any]) -> bool:
        """
        Run a task for an agent immediately (on-the-fly).

        Args:
            agent_id: The agent ID
            **kwargs: Arguments to pass to the task

        Returns:
            True if task was submitted successfully
        """
        if agent_id not in self.agents:
            logger.error(f"Agent {agent_id} not found")
            return False

        agent = self.agents[agent_id]
        await agent.submit_task(task_config)
        logger.info(f"Submitted on-the-fly task for agent {agent_id}")
        return True

    async def register_and_run(
        self, agent_id: str, task_config: Dict[str, Any]
    ) -> bool:
        """
        Register a task and run it immediately.

        Args:
            agent_id: The agent ID
            task_config: Task configuration
            **kwargs: Arguments to pass to the task

        Returns:
            True if registered and run successfully
        """
        if await self.register_task(agent_id, task_config):
            return await self.run_task_now(agent_id, task_config)
        return False

    async def _schedule_agent(self, agent_id: str, agent: BackgroundOmniCoreAgent):
        """Schedule an agent for execution."""
        try:
            task_config = await agent.get_task_config()

            interval = task_config.get("interval")

            self.scheduler.schedule_task(
                agent_id=agent_id,
                interval=interval,
                task_fn=agent.run_task,
                task_config=task_config,
            )
            logger.info(f"Scheduled agent {agent_id} with interval {interval}s")

        except Exception as e:
            logger.error(f"Failed to schedule agent {agent_id}: {e}")
            raise

    async def start(self):
        """Start the manager and all agents."""
        try:
            if self.is_running:
                logger.warning("Manager is already running")
                return

            self.scheduler.start()

            for agent_id, agent in self.agents.items():
                if not agent.is_worker_running:
                    await agent.start_worker()
                await self._schedule_agent(agent_id, agent)

            self.is_running = True
            logger.info("BackgroundAgentManager started successfully")

        except Exception as e:
            logger.error(f"Failed to start manager: {e}")
            raise

    async def shutdown(self):
        """Shutdown the manager and all agents."""
        try:
            if not self.is_running:
                logger.warning("Manager is not running")
                return

            self.scheduler.shutdown()

            for agent_id, agent in self.agents.items():
                try:
                    await agent.cleanup()
                    logger.info(f"Cleaned up agent {agent_id}")
                except Exception as e:
                    logger.error(f"Failed to cleanup agent {agent_id}: {e}")

            self.is_running = False
            logger.info("BackgroundAgentManager shutdown successfully")

        except Exception as e:
            logger.error(f"Failed to shutdown manager: {e}")
            raise

    async def get_agent_status(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a specific agent."""
        if agent_id not in self.agents:
            return None

        agent = self.agents[agent_id]
        status = await agent.get_status()

        status.update(
            {
                "manager_running": self.is_running,
                "scheduled": self.scheduler.is_task_scheduled(agent_id),
                "next_run": self.scheduler.get_next_run_time(agent_id),
                "task_registered": self.task_registry.exists(agent_id),
                "task_config": self.task_registry.get(agent_id)
                if self.task_registry.exists(agent_id)
                else None,
            }
        )

        return status

    async def get_manager_status(self) -> Dict[str, Any]:
        """Get overall manager status."""
        agent_statuses = {}
        running_count = 0
        paused_count = 0

        for agent_id in self.agents:
            status = await self.get_agent_status(agent_id)
            if status:
                agent_statuses[agent_id] = status
                if status.get("is_running"):
                    running_count += 1
                else:
                    paused_count += 1

        return {
            "manager_running": self.is_running,
            "total_agents": len(self.agents),
            "running_agents": running_count,
            "paused_agents": paused_count,
            "agents": list(self.agents.keys()),
            "total_tasks": len(self.task_registry.get_agent_ids()),
            "registered_tasks": self.task_registry.get_agent_ids(),
            "created_at": self.created_at.isoformat(),
            "memory_router": self.memory_router.get_memory_store_info(),
            "event_router": self.event_router.get_event_store_info(),
            "scheduler_running": self.scheduler.is_running(),
        }

    async def list_agents(self) -> List[str]:
        """List all agent IDs."""
        return list(self.agents.keys())

    async def pause_agent(self, agent_id: str):
        """Pause an agent (remove from scheduler)."""
        if agent_id not in self.agents:
            raise ValueError(f"Agent {agent_id} not found")

        try:
            self.scheduler.remove_task(agent_id)
            logger.info(f"Paused agent {agent_id}")

        except Exception as e:
            logger.error(f"Failed to pause agent {agent_id}: {e}")
            raise

    async def resume_agent(self, agent_id: str):
        """Resume an agent (add back to scheduler)."""
        if agent_id not in self.agents:
            raise ValueError(f"Agent {agent_id} not found")

        try:
            agent = self.agents[agent_id]
            await self._schedule_agent(agent_id, agent)
            logger.info(f"Resumed agent {agent_id}")

        except Exception as e:
            logger.error(f"Failed to resume agent {agent_id}: {e}")
            raise

    async def stop_agent(self, agent_id: str):
        """Stop a specific agent: unschedule and stop its worker loop."""
        if agent_id not in self.agents:
            raise ValueError(f"Agent {agent_id} not found")

        try:
            if self.scheduler.is_task_scheduled(agent_id):
                self.scheduler.remove_task(agent_id)

            agent = self.agents[agent_id]
            await agent.stop_worker()
            logger.info(f"Stopped worker for agent {agent_id}")

        except Exception as e:
            logger.error(f"Failed to stop agent {agent_id}: {e}")
            raise

    async def start_agent(self, agent_id: str):
        """Start (schedule) a specific agent. Ensures manager/scheduler is running."""
        if agent_id not in self.agents:
            raise ValueError(f"Agent {agent_id} not found")

        try:
            if not self.is_running:
                await self.start()

            agent = self.agents[agent_id]

            # Ensure worker is running
            if not agent.is_worker_running:
                await agent.start_worker()

            if self.scheduler.is_task_scheduled(agent_id):
                self.scheduler.remove_task(agent_id)
            await self._schedule_agent(agent_id, agent)
            logger.info(f"Started (scheduled) agent {agent_id}")

        except Exception as e:
            logger.error(f"Failed to start agent {agent_id}: {e}")
            raise

    async def update_agent_config(self, agent_id: str, new_config: Dict[str, Any]):
        """Update agent configuration."""
        if agent_id not in self.agents:
            raise ValueError(f"Agent {agent_id} not found")

        try:
            agent = self.agents[agent_id]
            await agent.update_config(new_config)

            self.agent_configs[agent_id].update(new_config)

            if self.is_running:
                self.scheduler.remove_task(agent_id)
                await self._schedule_agent(agent_id, agent)

            logger.info(f"Updated configuration for agent {agent_id}")

        except Exception as e:
            logger.error(f"Failed to update agent {agent_id} config: {e}")
            raise

    async def delete_agent(self, agent_id: str):
        """Delete an agent."""
        if agent_id not in self.agents:
            raise ValueError(f"Agent {agent_id} not found")

        try:
            if self.is_running:
                self.scheduler.remove_task(agent_id)

            if self.task_registry.exists(agent_id):
                self.task_registry.remove(agent_id)

            agent = self.agents[agent_id]
            await agent.cleanup()

            del self.agents[agent_id]
            del self.agent_configs[agent_id]

            logger.info(f"Deleted agent {agent_id}")

        except Exception as e:
            logger.error(f"Failed to delete agent {agent_id}: {e}")
            raise

    async def get_agent_event_info(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get event streaming information for an agent."""
        if agent_id not in self.agents:
            return None

        agent = self.agents[agent_id]
        return await agent.get_event_stream_info()

    async def get_all_event_info(self) -> Dict[str, Any]:
        """Get event streaming information for all agents."""
        event_info = {}
        for agent_id, agent in self.agents.items():
            event_info[agent_id] = await agent.get_event_stream_info()

        return {
            "agents": event_info,
            "shared_event_store": self.event_router.get_event_store_info(),
            "shared_memory_store": self.memory_router.get_memory_store_info(),
        }

    async def get_agent(self, agent_id: str) -> Optional[BackgroundOmniCoreAgent]:
        """Get a specific agent instance."""
        return self.agents.get(agent_id)

    async def get_agent_session_id(self, agent_id: str) -> Optional[str]:
        """Get the session ID for a specific agent."""
        if agent_id not in self.agents:
            return None

        agent = self.agents[agent_id]
        return await agent.get_session_id()

    async def get_all_session_ids(self) -> Dict[str, str]:
        """Get session IDs for all agents."""
        session_ids = {}
        for agent_id, agent in self.agents.items():
            session_ids[agent_id] = await agent.get_session_id()

        return session_ids

    async def is_agent_running(self, agent_id: str) -> bool:
        """Check if a specific agent is currently running."""
        if agent_id not in self.agents:
            return False

        agent = self.agents[agent_id]
        return agent.is_running

    async def get_running_agents(self) -> List[str]:
        """Get list of currently running agents."""
        running_agents = []
        for agent_id, agent in self.agents.items():
            if agent.is_running:
                running_agents.append(agent_id)

        return running_agents

    async def get_agent_metrics(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get metrics for a specific agent."""
        if agent_id not in self.agents:
            return None

        agent = self.agents[agent_id]
        task_config = await agent.get_task_config()
        return {
            "agent_id": agent_id,
            "run_count": agent.run_count,
            "error_count": agent.error_count,
            "last_run": agent.last_run.isoformat() if agent.last_run else None,
            "is_running": agent.is_running,
            "interval": task_config.get("interval"),
            "max_retries": task_config.get("max_retries"),
            "retry_delay": task_config.get("retry_delay"),
            "has_task": await agent.has_task(),
            "task_query": await agent.get_task_query()
            if await agent.has_task()
            else None,
        }

    async def get_all_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Get metrics for all agents."""
        metrics = {}
        for agent_id in self.agents:
            agent_metrics = await self.get_agent_metrics(agent_id)
            if agent_metrics:
                metrics[agent_id] = agent_metrics

        return metrics
