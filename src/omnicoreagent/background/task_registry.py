"""
Task registry for managing background agent task definitions.
"""

from typing import Dict, List, Optional
from omnicoreagent.core.utils import logger
from pydantic import BaseModel
import uuid


class TaskConfig(BaseModel):
    """Task configuration."""

    query: str
    interval: int = 3600
    max_retries: int = 3
    retry_delay: int = 60
    timeout: Optional[int] = 300
    queue_size: int = 100
    session_id: str = str(uuid.uuid4())


class TaskRegistry:
    """Registry for storing and managing task definitions."""

    def __init__(self):
        self._tasks: Dict[str, Dict] = {}

    def register(self, agent_id: str, config: Dict):
        """Register a new task configuration."""
        try:
            if not isinstance(config, dict):
                if hasattr(config, "model_dump"):
                    config = config.model_dump()
                else:
                    config = dict(config)

            self._tasks[agent_id] = config
            logger.info(f"Registered task for agent: {agent_id}")
        except Exception as e:
            logger.error(f"Failed to register task for agent {agent_id}: {e}")
            raise

    def get(self, agent_id: str) -> Optional[Dict]:
        """Get task configuration for an agent."""
        return self._tasks.get(agent_id)

    def all_tasks(self) -> List[Dict]:
        """Get all registered task configurations."""
        return list(self._tasks.values())

    def remove(self, agent_id: str):
        """Remove a task configuration."""
        try:
            if agent_id in self._tasks:
                del self._tasks[agent_id]
                logger.info(f"Removed task for agent: {agent_id}")
        except Exception as e:
            logger.error(f"Failed to remove task for agent {agent_id}: {e}")
            raise

    def exists(self, agent_id: str) -> bool:
        """Check if a task exists for the given agent ID."""
        return agent_id in self._tasks

    def update(self, agent_id: str, config: Dict):
        """Update an existing task configuration."""
        if agent_id in self._tasks:
            if not isinstance(config, dict):
                if hasattr(config, "model_dump"):
                    config = config.model_dump()
                else:
                    config = dict(config)

            self._tasks[agent_id].update(config)
            logger.info(f"Updated task for agent: {agent_id}")
        else:
            raise KeyError(f"Task for agent '{agent_id}' not found in registry")

    def get_agent_ids(self) -> List[str]:
        """Get all registered agent IDs."""
        return list(self._tasks.keys())

    def clear(self):
        """Clear all registered tasks."""
        self._tasks.clear()
        logger.info("Cleared all registered tasks")
