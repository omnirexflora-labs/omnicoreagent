from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseToolHandler(ABC):
    """Provider-specific tool execution boundary."""

    @abstractmethod
    async def call(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        """Execute a validated tool call."""
        raise NotImplementedError
