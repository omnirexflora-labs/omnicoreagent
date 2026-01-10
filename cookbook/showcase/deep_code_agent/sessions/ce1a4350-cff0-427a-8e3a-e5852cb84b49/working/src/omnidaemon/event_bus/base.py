from abc import ABC, abstractmethod
from typing import Any, Callable, Dict


class BaseEventBus(ABC):
    """Abstract event bus contract. Implement drivers for RedisStreams/Kafka/NATS/RabbitMQ."""

    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def publish(self, event_payload: Dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def subscribe(
        self,
        topic: str,
        agent_name: str,
        callback: Callable[[dict], Any],
        config: Dict[str, Any] = {},
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def unsubscribe(self, topic: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_consumers(self) -> Dict[str, Any]:
        """Return current consumers and their configurations."""
        raise NotImplementedError
