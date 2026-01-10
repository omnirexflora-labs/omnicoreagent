import asyncio
import logging
from typing import Optional, Dict, Any, List, Optional, Tuple, Callable
from uuid import uuid4
import aiohttp
from omnidaemon.event_bus.factory import EventBusFactory
from omnidaemon.result_store import ResultStore

logger = logging.getLogger(__name__)


class BaseAgentRunner:
    """
     - Supports multiple topic subscriptions and agent callbacks.
    - Each callback can be an async or sync function.
    - Uses a single event bus instance per runner process.
    """

    def __init__(
        self,
        runner_id: Optional[str] = None,
        result_store: Optional[ResultStore] = None,
    ):
        self.runner_id = runner_id or str(uuid4())
        self.bus = None
        self._running = False
        self._handlers: Dict[str, List[Dict[str, Any]]] = {}
        self.result_store = result_store
        self._metrics: Dict[Tuple, Any] = {}

    async def register_handler(
        self, topic: str, subscription: Dict[str, Any], config: Dict[str, Any] = {}
    ):
        """
        Register an agent handler for a given topic.
        Automatically subscribes to the topic on the event bus.
        adding config for redis stream like both the reclaim_idle_ms and dlq_retry_limit because we want to be flexible
        """
        if not self.bus:
            self.bus = await EventBusFactory.get_event_bus()
            await self.bus.connect()

        if topic not in self._handlers:
            self._handlers[topic] = []
        self._handlers[topic].append(subscription)
        agent_name = subscription.get("name")
        agent_callback = subscription.get("callback")
        general_callback = await self._make_agent_callback(
            topic=topic, agent_name=agent_name, agent_callback=agent_callback
        )
        await self.bus.subscribe(
            topic=topic, callback=general_callback, config=config, agent_name=agent_name
        )
        logger.info(f"[Runner {self.runner_id}] Subscribed to topic '{topic}'")

    async def _make_agent_callback(
        self, topic: str, agent_name: str, agent_callback: Callable
    ):
        """Returns a callback that only runs one agent."""

        async def agent_wrapper(message: Dict[str, Any]):
            if "topic" not in message:
                message = {**message, "topic": topic}

            key = (topic, agent_name)
            self._metrics[key]["tasks_received"] += 1

            try:
                logger.debug(
                    f"[Runner {self.runner_id}] Handling message on '{topic}' with {agent_name}"
                )
                start_time = asyncio.get_event_loop().time()
                result = await self._maybe_await(agent_callback(message))
                await self._send_response(message, result)
                self._metrics[key]["tasks_processed"] += 1
                self._metrics[key]["total_processing_time"] += (
                    asyncio.get_event_loop().time() - start_time
                )
            except Exception as e:
                self._metrics[key]["tasks_failed"] += 1
                logger.exception(
                    f"[Runner {self.runner_id}] Error in agent '{agent_name}': {e}"
                )
                raise

        return agent_wrapper

    async def publish(self, event_payload: Dict[str, Any]):
        """
        publish to the event bus
        """
        if not self.bus:
            self.bus = await EventBusFactory.get_event_bus()
            await self.bus.connect()
        await self.bus.publish(event_payload=event_payload)

    async def _send_response(self, message: Dict[str, Any], result: Any):
        webhook_url = message.get("webhook")
        task_id = message.get("task_id")
        topic = message.get("topic")

        response_payload = {
            "runner_id": self.runner_id,
            "topic": topic,
            "task_id": task_id,
            "status": "completed",
            "result": result,
            "timestamp": asyncio.get_event_loop().time(),
        }

        if task_id and self.result_store:
            try:
                await self.result_store.save_result(task_id, response_payload)
                logger.info(f"Result saved for task {task_id}")
            except Exception as e:
                logger.error(f"Failed to save result for {task_id}: {e}")

        # Send webhook if requested
        if webhook_url:
            MAX_RETRIES = 3
            BACKOFF_FACTOR = 2

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            webhook_url,
                            json={"payload": response_payload},
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            logger.info(
                                f"Webhook sent to {webhook_url} [status={resp.status}]"
                            )
                            break
                except Exception as e:
                    logger.error(
                        f"Webhook attempt {attempt}/{MAX_RETRIES} failed for task {task_id}: {e}"
                    )
                    if attempt < MAX_RETRIES:
                        delay = BACKOFF_FACTOR**attempt
                        logger.info(f"Retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                    else:
                        logger.critical(
                            f"Webhook failed permanently for task {task_id} after {MAX_RETRIES} attempts"
                        )

            logger.info(f"Task {task_id} completed. Webhook delivery finalized.")
        else:
            logger.debug(f"Task {task_id} completed (no webhook). Result stored.")

    async def start(self):
        """Start listening for all registered topics."""
        if self._running:
            logger.warning(f"[Runner {self.runner_id}] Already running.")
            return
        self._running = True
        logger.info(
            f"[Runner {self.runner_id}] Listening for topics: {list(self._handlers.keys())}"
        )

    async def stop(self):
        """Stop runner and close event bus."""
        if not self._running:
            return
        await self.bus.close()
        self._running = False
        logger.info(f"[Runner {self.runner_id}] Stopped.")

    @staticmethod
    async def _maybe_await(result):
        """Await coroutine results automatically."""
        if asyncio.iscoroutine(result):
            return await result
        return result
