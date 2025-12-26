"""
Background OmniCoreAgent for self-flying automation.
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime

from omnicoreagent.omni_agent.agent import OmniCoreAgent

from omnicoreagent.core.memory_store.memory_router import MemoryRouter

from omnicoreagent.core.utils import logger
from omnicoreagent.core.events.event_router import EventRouter
from omnicoreagent.core.events.base import (
    Event,
    EventType,
    BackgroundTaskStartedPayload,
    BackgroundTaskCompletedPayload,
    BackgroundTaskErrorPayload,
    BackgroundAgentStatusPayload,
)
from omnicoreagent.omni_agent.background_agent.task_registry import TaskRegistry


class BackgroundOmniCoreAgent(OmniCoreAgent):
    """Background OmniCoreAgent for automated task execution."""

    def __init__(
        self,
        config: Dict[str, Any],
        task_registry: TaskRegistry,
        memory_router: Optional[MemoryRouter] = None,
        event_router: Optional[EventRouter] = None,
    ):
        """
        Initialize BackgroundOmniCoreAgent.

        Args:
            config: Configuration dictionary containing agent setup
            memory_store: Optional memory store
            event_router: Optional event router for event streaming
            task_registry: TaskRegistry instance (required for task management)
        """
        agent_config = config.get("agent_config", {})
        model_config = config.get("model_config", {})
        mcp_tools = config.get("mcp_tools", [])
        local_tools = config.get("local_tools", None)

        super().__init__(
            name=config.get("agent_id", f"background_agent_{uuid.uuid4().hex[:8]}"),
            system_instruction=config.get(
                "system_instruction",
                "You are a background agent that executes tasks automatically.",
            ),
            model_config=model_config,
            mcp_tools=mcp_tools,
            local_tools=local_tools,
            agent_config=agent_config,
            memory_router=memory_router,
            event_router=event_router,
            debug=config.get("debug", False),
        )

        self.agent_id = config.get("agent_id", self.name)
        self.interval = config.get("interval", 3600)
        self.max_retries = config.get("max_retries", 3)
        self.retry_delay = config.get("retry_delay", 60)

        if task_registry is None:
            raise ValueError("TaskRegistry is required for BackgroundOmniCoreAgent")
        self.task_registry = task_registry

        self.session_id = f"background_{self.agent_id}_{uuid.uuid4().hex[:8]}"

        self.is_running = False
        self.last_run = None
        self.run_count = 0
        self.error_count = 0

        # Task Queue for reliable execution
        self._task_queue = asyncio.Queue()
        self._worker_task = None
        self._shutdown_event = asyncio.Event()

        logger.info(
            f"Initialized BackgroundOmniCoreAgent: {self.agent_id} with session_id: {self.session_id}"
        )

    async def connect_mcp_servers(self):
        """Connect to MCP servers if not already connected."""
        await super().connect_mcp_servers()
        logger.info(f"BackgroundOmniCoreAgent {self.agent_id} connected to MCP servers")

    def get_session_id(self) -> str:
        """Get the persistent session ID for this background agent."""
        return self.session_id

    def get_event_stream_info(self) -> Dict[str, Any]:
        """Get information needed for event streaming setup."""
        return {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "event_store_type": self.get_event_store_type(),
            "event_store_available": self.is_event_store_available(),
            "event_store_info": self.get_event_store_info(),
        }

    async def stream_events(self, session_id: str):
        """Stream events for this background agent (consistent with OmniCoreAgent API)."""
        async for event in self.event_router.stream(session_id=session_id):
            yield event

    async def get_events(self, session_id: str) -> List[Event]:
        """Get events for this background agent (consistent with OmniCoreAgent API)."""
        return await self.event_router.get_events(session_id=session_id)

    def get_task_query(self) -> str:
        """Get the task query from TaskRegistry."""
        if not self.task_registry.exists(self.agent_id):
            raise ValueError(
                f"No task registered for agent {self.agent_id}. Use TaskRegistry to register a task first."
            )

        task_config = self.task_registry.get(self.agent_id)
        if not task_config or "query" not in task_config:
            raise ValueError(f"Task for agent {self.agent_id} is missing 'query' field")

        logger.info(f"Using task query from TaskRegistry for agent {self.agent_id}")
        return task_config["query"]

    def get_task_config(self) -> Dict[str, Any]:
        """Get the complete task configuration from TaskRegistry."""
        if not self.task_registry.exists(self.agent_id):
            raise ValueError(
                f"No task registered for agent {self.agent_id}. Use TaskRegistry to register a task first."
            )

        task_config = self.task_registry.get(self.agent_id)
        if not task_config:
            raise ValueError(f"Task configuration not found for agent {self.agent_id}")

        logger.info(f"Using task config from TaskRegistry for agent {self.agent_id}")
        return task_config
    async def start_worker(self):
        """Start the background worker loop."""
        if self._worker_task is not None:
            logger.warning(f"Worker for agent {self.agent_id} is already running")
            return

        self._shutdown_event.clear()
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info(f"Started worker for agent {self.agent_id}")

    async def _worker_loop(self):
        """Background worker loop that processes tasks from the queue."""
        logger.info(f"Worker loop started for agent {self.agent_id}")
        while not self._shutdown_event.is_set():
            try:
                # Use wait_for to check shutdown_event periodically
                try:
                    task_data = await asyncio.wait_for(self._task_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                logger.info(f"Worker for {self.agent_id} picked up a task")
                kwargs = task_data.get("kwargs", {})
                
                try:
                    await self._internal_run_task(**kwargs)
                finally:
                    self._task_queue.task_done()
                    
            except asyncio.CancelledError:
                logger.info(f"Worker for {self.agent_id} cancelled")
                break
            except Exception as e:
                logger.error(f"Error in worker loop for {self.agent_id}: {e}")
                await asyncio.sleep(1)  # Prevent tight error loop

        logger.info(f"Worker loop exited for agent {self.agent_id}")

    async def submit_task(self, **kwargs):
        """Submit a task to the queue for execution."""
        await self._task_queue.put({"kwargs": kwargs, "timestamp": datetime.now().isoformat()})
        logger.info(f"Task submitted for agent {self.agent_id}")

    async def run_task(self, **kwargs):
        """
        Trigger for APScheduler or manual calls. 
        Puts a task in the queue instead of running immediately.
        """
        await self.submit_task(**kwargs)

    async def _internal_run_task(self, **kwargs):
        """The actual task execution logic."""
        if self.is_running:
            logger.warning(
                f"Agent {self.agent_id} is already running, skipping execution"
            )
            return

        if not self.has_task():
            raise ValueError(
                f"No task registered for agent {self.agent_id}. Register a task first using TaskRegistry."
            )

        self.is_running = True
        task_session_id = self.session_id

        try:
            task_started_event = Event(
                type=EventType.BACKGROUND_TASK_STARTED,
                payload=BackgroundTaskStartedPayload(
                    agent_id=self.agent_id,
                    session_id=task_session_id,
                    timestamp=datetime.now().isoformat(),
                    run_count=self.run_count + 1,
                    kwargs=kwargs,
                ),
                agent_name=self.agent_id,
            )
            await self.event_router.append(
                session_id=task_session_id, event=task_started_event
            )

            status_event = Event(
                type=EventType.BACKGROUND_AGENT_STATUS,
                payload=BackgroundAgentStatusPayload(
                    agent_id=self.agent_id,
                    status="running",
                    session_id=task_session_id,
                    timestamp=datetime.now().isoformat(),
                ),
                agent_name=self.agent_id,
            )
            await self.event_router.append(
                session_id=task_session_id, event=status_event
            )

            result = await self._execute_with_retries(**kwargs)

            self.run_count += 1
            self.last_run = datetime.now()

            task_completed_event = Event(
                type=EventType.BACKGROUND_TASK_COMPLETED,
                payload=BackgroundTaskCompletedPayload(
                    agent_id=self.agent_id,
                    session_id=task_session_id,
                    timestamp=datetime.now().isoformat(),
                    run_count=self.run_count,
                    result=result,
                ),
                agent_name=self.agent_id,
            )
            await self.event_router.append(
                session_id=task_session_id, event=task_completed_event
            )

            status_event = Event(
                type=EventType.BACKGROUND_AGENT_STATUS,
                payload=BackgroundAgentStatusPayload(
                    agent_id=self.agent_id,
                    status="idle",
                    last_run=self.last_run.isoformat(),
                    run_count=self.run_count,
                    timestamp=datetime.now().isoformat(),
                ),
                agent_name=self.agent_id,
            )
            await self.event_router.append(
                session_id=task_session_id, event=status_event
            )

            logger.info(f"Background task completed for agent {self.agent_id}")
            return result

        except Exception as e:
            self.error_count += 1

            error_event = Event(
                type=EventType.BACKGROUND_TASK_ERROR,
                payload=BackgroundTaskErrorPayload(
                    agent_id=self.agent_id,
                    session_id=task_session_id,
                    timestamp=datetime.now().isoformat(),
                    error=str(e),
                    error_count=self.error_count,
                ),
                agent_name=self.agent_id,
            )
            await self.event_router.append(
                session_id=task_session_id, event=error_event
            )

            status_event = Event(
                type=EventType.BACKGROUND_AGENT_STATUS,
                payload=BackgroundAgentStatusPayload(
                    agent_id=self.agent_id,
                    status="error",
                    last_run=self.last_run.isoformat() if self.last_run else None,
                    run_count=self.run_count,
                    error_count=self.error_count,
                    timestamp=datetime.now().isoformat(),
                ),
                agent_name=self.agent_id,
            )
            await self.event_router.append(
                session_id=task_session_id, event=status_event
            )

            logger.error(f"Background task failed for agent {self.agent_id}: {e}")
            raise

        finally:
            self.is_running = False

    async def _execute_with_retries(self, **kwargs):
        """Execute task with retry logic."""
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                task_query = kwargs.get("query") or self.get_task_query()

                result = await self.run(
                    query=task_query,
                    session_id=self.session_id,
                )

                return result

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Attempt {attempt + 1} failed for agent {self.agent_id}: {e}"
                )

                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay)
                else:
                    break

        raise last_error

    def get_status(self) -> Dict[str, Any]:
        """Get current status of the background agent."""
        return {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "is_running": self.is_running,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "run_count": self.run_count,
            "error_count": self.error_count,
            "interval": self.interval,
            "max_retries": self.max_retries,
            "available_tools": self._get_available_tools(),
            "event_router": self.get_event_store_info(),
            "memory_router": self.memory_router.get_memory_store_info()
            if hasattr(self.memory_router, "get_memory_store_info")
            else {"type": "default"},
            "event_stream_info": self.get_event_stream_info(),
            "has_task": self.has_task(),
            "current_task_query": self.get_task_query() if self.has_task() else None,
        }

    def _get_available_tools(self) -> Dict[str, Any]:
        """Get information about available tools."""
        tools_info = {"mcp_tools": [], "local_tools": []}

        if self.mcp_client and self.mcp_client.available_tools:
            tools_info["mcp_tools"] = list(self.mcp_client.available_tools.keys())

        if self.local_tools:
            tools_info["local_tools"] = self.local_tools.list_tools()

        return tools_info

    async def update_config(self, new_config: Dict[str, Any]):
        """Update agent configuration."""
        try:
            if "interval" in new_config:
                self.interval = new_config["interval"]
            if "max_retries" in new_config:
                self.max_retries = new_config["max_retries"]
            if "retry_delay" in new_config:
                self.retry_delay = new_config["retry_delay"]

            logger.info(f"Updated configuration for agent {self.agent_id}")

        except Exception as e:
            logger.error(
                f"Failed to update configuration for agent {self.agent_id}: {e}"
            )
            raise

    async def cleanup(self):
        """Clean up background agent resources."""
        try:
            logger.info(f"Cleaning up background agent {self.agent_id}")
            
            self._shutdown_event.set()
            if self._worker_task:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Error while canceling worker for {self.agent_id}: {e}")
                self._worker_task = None

            await super().cleanup()

            logger.info(f"Cleaned up background agent {self.agent_id}")

        except Exception as e:
            logger.error(f"Failed to cleanup background agent {self.agent_id}: {e}")
            raise

    def has_task(self) -> bool:
        """Check if the agent has a task registered."""
        return self.task_registry.exists(self.agent_id)
