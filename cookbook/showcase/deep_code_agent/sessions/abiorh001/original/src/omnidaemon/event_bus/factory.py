import importlib
import logging
from typing import Optional, Type
from decouple import config


from omnidaemon.event_bus.base import BaseEventBus

logger = logging.getLogger(__name__)


class EventBusFactory:
    """
    Factory that creates and manages a singleton instance of the active EventBus.

    Loads the event bus type dynamically based on environment configuration.

    Example:
        EVENT_BUS_TYPE=redis_stream
        REDIS_URL=redis://localhost:6379/0

    Usage:
        bus = await EventBusFactory.get_event_bus()
        await bus.publish("topic", {"key": "value"})
    """

    _instance: Optional[BaseEventBus] = None
    _backend_name: Optional[str] = None

    @classmethod
    async def get_event_bus(cls) -> BaseEventBus:
        """Return an initialized and connected event bus instance."""
        if cls._instance:
            return cls._instance

        backend = config("EVENT_BUS_TYPE").lower()
        cls._backend_name = backend
        logger.info(f"[EventBusFactory] Initializing event bus backend: {backend}")

        # if backend == "redis_pub_sub":
        #     cls._instance = await cls._load_backend(
        #         "omnidaemon.event_bus.redis_pub_sub", "RedisPubSubEventBus"
        #     )
        if backend == "redis_stream":
            cls._instance = await cls._load_backend(
                "omnidaemon.event_bus.redis_stream_bus", "RedisStreamEventBus"
            )
        elif backend == "rabbitmq":
            cls._instance = await cls._load_backend(
                "omnidaemon.event_bus.rabbitmq_bus", "RabbitMQEventBus"
            )
        elif backend == "kafka":
            cls._instance = await cls._load_backend(
                "omnidaemon.event_bus.kafka_bus", "KafkaEventBus"
            )
        else:
            raise ValueError(f"Unsupported event bus backend: {backend}")

        # Connect to the backend before returning
        await cls._instance.connect()
        return cls._instance

    @classmethod
    async def _load_backend(cls, module_path: str, class_name: str) -> BaseEventBus:
        """Dynamically import and instantiate the backend event bus."""
        try:
            module = importlib.import_module(module_path)
            backend_class: Type[BaseEventBus] = getattr(module, class_name)
            instance = backend_class()
            logger.debug(
                f"[EventBusFactory] Loaded backend: {module_path}.{class_name}"
            )
            return instance
        except (ImportError, AttributeError) as e:
            raise ImportError(
                f"Failed to load event bus backend {module_path}.{class_name}: {e}"
            )

    @classmethod
    async def shutdown(cls):
        """Gracefully close the event bus connection."""
        if cls._instance:
            await cls._instance.close()
            logger.info(f"[EventBusFactory] {cls._backend_name} event bus shut down.")
            cls._instance = None
