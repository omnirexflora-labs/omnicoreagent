"""
Background OmniCoreAgent for self-flying automation.
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

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

        if task_registry is None:
            raise ValueError("TaskRegistry is required for BackgroundOmniCoreAgent")
        self.task_registry = task_registry

        self.agent_id = config.get("agent_id", self.name)
        self.is_running = False
        self.last_run = None
        self.run_count = 0
        self.error_count = 0

        # Task Queue for reliable execution
        queue_size = config.get("queue_size", 100)
        self._task_queue = asyncio.Queue(maxsize=queue_size)
        self._worker_task = None
        self._shutdown_event = asyncio.Event()

        logger.info(f"Initialized BackgroundOmniCoreAgent: {self.agent_id}")

    async def connect_mcp_servers(self):
        """Connect to MCP servers if not already connected."""
        await super().connect_mcp_servers()
        logger.info(f"BackgroundOmniCoreAgent {self.agent_id} connected to MCP servers")

    async def get_session_id(self) -> str:
        """Get the persistent session ID for this background agent."""
        task_config = await self.get_task_config()
        return task_config.get("session_id")

    async def get_event_stream_info(self) -> Dict[str, Any]:
        """Get information needed for event streaming setup."""
        return {
            "agent_id": self.agent_id,
            "session_id": await self.get_session_id(),
            "event_store_type": await self.get_event_store_type(),
            "event_store_available": await self.is_event_store_available(),
            "event_store_info": await self.get_event_store_info(),
        }

    async def stream_events(self, session_id: str):
        """Stream events for this background agent (consistent with OmniCoreAgent API)."""
        async for event in self.event_router.stream(session_id=session_id):
            yield event

    async def get_events(self, session_id: str) -> List[Event]:
        """Get events for this background agent (consistent with OmniCoreAgent API)."""
        return await self.event_router.get_events(session_id=session_id)

    async def get_task_query(self) -> str:
        """Get the task query from TaskRegistry."""
        if not self.task_registry.exists(self.agent_id):
            raise ValueError(
                f"No task registered for agent {self.agent_id}. Use TaskRegistry to register a task first."
            )

        task_config = self.task_registry.get(self.agent_id)
        if not task_config or "query" not in task_config:
            raise ValueError(f"Task for agent {self.agent_id} is missing 'query' field")

        logger.info(f"Using task query from TaskRegistry for agent {self.agent_id}")
        return task_config.get("query")

    async def get_task_config(self) -> Dict[str, Any]:
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

    @property
    def is_worker_running(self) -> bool:
        """Check if the background worker loop is running."""
        return self._worker_task is not None and not self._worker_task.done()

    async def start_worker(self):
        """Start the background worker loop."""
        if self.is_worker_running:
            logger.warning(f"Worker for agent {self.agent_id} is already running")
            return

        self._shutdown_event.clear()
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info(f"Started worker for agent {self.agent_id}")

    async def stop_worker(self):
        """Stop the background worker loop."""
        if not self.is_worker_running:
            logger.warning(f"Worker for agent {self.agent_id} is not running")
            return

        logger.info(f"Stopping worker for agent {self.agent_id}")
        self._shutdown_event.set()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                # Wait for worker to finish current task or cancelled
                await self._worker_task
                
                # Wait for queue to process if possible (graceful shutdown)
                if not self._task_queue.empty():
                    logger.warning(
                        f"Agent {self.agent_id} stopping with {self._task_queue.qsize()} pending tasks"
                    )
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error while stopping worker for {self.agent_id}: {e}")
            self._worker_task = None

    async def _worker_loop(self):
        """Background worker loop that processes tasks from the queue."""
        logger.info(f"Worker loop started for agent {self.agent_id}")
        while not self._shutdown_event.is_set():
            try:
                # Use wait_for to check shutdown_event periodically
                try:
                    task_data = await asyncio.wait_for(
                        self._task_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                logger.info(f"Worker for {self.agent_id} picked up a task")
                task_config = task_data.get("kwargs", {})

                try:
                    await self._internal_run_task(task_config=task_config)
                finally:
                    self._task_queue.task_done()

            except asyncio.CancelledError:
                logger.info(f"Worker for {self.agent_id} cancelled")
                break
            except Exception as e:
                logger.error(f"Error in worker loop for {self.agent_id}: {e}")
                await asyncio.sleep(1)  # Prevent tight error loop

        logger.info(f"Worker loop exited for agent {self.agent_id}")

    async def submit_task(self, task_config: Dict[str, Any]):
        """Submit a task to the queue for execution."""
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            # Non-blocking put if queue is full, or wait with timeout?
            # Background tasks usually want to be non-blocking to the caller.
            # Using wait_for to allow small backpressure but fail if overloaded.
            
            queue_timeout = task_config.get("queue_timeout", 5.0)
            
            await asyncio.wait_for(
                self._task_queue.put(
                    {"kwargs": task_config, "timestamp": timestamp}
                ),
                timeout=queue_timeout
            )
            logger.info(f"Task submitted for agent {self.agent_id}")
            
        except asyncio.TimeoutError:
            logger.error(f"Task queue full for agent {self.agent_id}, dropping task")
            raise  # Re-raise so caller knows it failed
        except Exception as e:
            logger.error(f"Failed to submit task for agent {self.agent_id}: {e}")
            raise

    async def run_task(self, task_config: Dict[str, Any]):
        """
        Trigger for APScheduler or manual calls.
        Puts a task in the queue instead of running immediately.
        """
        await self.submit_task(task_config)

    async def _internal_run_task(self, task_config: Dict[str, Any]):
        """The actual task execution logic."""
        if self.is_running:
            logger.warning(
                f"Agent {self.agent_id} is already running, skipping execution"
            )
            return

        if not await self.has_task():
            raise ValueError(
                f"No task registered for agent {self.agent_id}. Register a task first using TaskRegistry."
            )

        self.is_running = True
        task_session_id = task_config.get("session_id") or str(uuid.uuid4())

        try:
            task_started_event = Event(
                type=EventType.BACKGROUND_TASK_STARTED,
                payload=BackgroundTaskStartedPayload(
                    agent_id=self.agent_id,
                    session_id=task_session_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    run_count=self.run_count + 1,
                    kwargs=task_config,
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
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ),
                agent_name=self.agent_id,
            )
            await self.event_router.append(
                session_id=task_session_id, event=status_event
            )

            # Get timeout from task config
            timeout = task_config.get("timeout", 300)  # Default 5 minutes
            
            # Execute with timeout
            try:
                result = await asyncio.wait_for(
                    self._execute_with_retries(task_config),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                raise TimeoutError(f"Task execution timed out after {timeout} seconds")

            self.run_count += 1
            self.last_run = datetime.now(timezone.utc)

            task_completed_event = Event(
                type=EventType.BACKGROUND_TASK_COMPLETED,
                payload=BackgroundTaskCompletedPayload(
                    agent_id=self.agent_id,
                    session_id=task_session_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
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
                    timestamp=datetime.now(timezone.utc).isoformat(),
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
                    timestamp=datetime.now(timezone.utc).isoformat(),
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
                    timestamp=datetime.now(timezone.utc).isoformat(),
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

    async def _execute_with_retries(self, task_config: Dict[str, Any]):
        """Execute task with retry logic."""
        last_error = None
        max_retries = task_config.get("max_retries", 3)

        for attempt in range(max_retries + 1):
            try:
                task_query = task_config.get("query")
                session_id = task_config.get("session_id")

                result = await self.run(
                    query=task_query,
                    session_id=session_id,
                )

                return result

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Attempt {attempt + 1} failed for agent {self.agent_id}: {e}"
                )

                if attempt < max_retries:
                    retry_delay = task_config.get("retry_delay", 60)
                    await asyncio.sleep(retry_delay)
                else:
                    break

        raise last_error

    async def get_status(self) -> Dict[str, Any]:
        """Get current status of the background agent."""
        get_task_config = self.task_registry.get(self.agent_id)
        if not get_task_config:
            raise ValueError(f"Task configuration not found for agent {self.agent_id}")

        return {
            "agent_id": self.agent_id,
            "session_id": await self.get_session_id(),
            "is_running": self.is_running,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "run_count": self.run_count,
            "error_count": self.error_count,
            "interval": get_task_config.get("interval"),
            "max_retries": get_task_config.get("max_retries"),
            "is_worker_running": self.is_worker_running,
            "available_tools": await self._get_available_tools(),
            "event_router": await self.get_event_store_info(),
            "memory_router": self.memory_router.get_memory_store_info()
            if hasattr(self.memory_router, "get_memory_store_info")
            else {"type": "default"},
            "event_stream_info": await self.get_event_stream_info(),
            "has_task": await self.has_task(),
            "current_task_query": await self.get_task_query()
            if await self.has_task()
            else None,
            "queue_size": self._task_queue.qsize(),
        }

    async def _get_available_tools(self) -> Dict[str, Any]:
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
            get_task_config = self.task_registry.get(self.agent_id)
            if not get_task_config:
                raise ValueError(
                    f"Task configuration not found for agent {self.agent_id}"
                )

            for key, value in new_config.items():
                if key in get_task_config:
                    get_task_config[key] = value

            self.task_registry.update(self.agent_id, get_task_config)

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

            await self.stop_worker()

            await super().cleanup()

            logger.info(f"Cleaned up background agent {self.agent_id}")

        except Exception as e:
            logger.error(f"Failed to cleanup background agent {self.agent_id}: {e}")
            raise

    async def has_task(self) -> bool:
        """Check if the agent has a task registered."""
        return self.task_registry.exists(self.agent_id)
