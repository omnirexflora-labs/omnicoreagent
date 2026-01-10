from redis import asyncio as aioredis
from decouple import config
from omnidaemon.event_bus.base import BaseEventBus
from typing import Optional, Callable, Dict, Any
import asyncio
import json
import logging

logger = logging.getLogger(__name__)


class RedisPubSubEventBus(BaseEventBus):
    """
    Async Redis pub/sub event bus that supports multiple channel subscriptions
    and dispatches messages to registered callbacks.

    - subscribe(channel, callback) registers a callback (sync or async).
    - publish(channel, message) publishes JSON-serializable messages.
    - single background listener loop dispatches to callbacks.
    """

    def __init__(self, redis_url: str = config("REDIS_URL")):
        self.redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None
        self._running = False
        self._listener_task: Optional[asyncio.Task] = None
        # channel -> list[callable]
        self._callbacks: Dict[str, List[Callable[[dict], Any]]] = {}
        # protect connect operations
        self._connect_lock = asyncio.Lock()

    # ----------------
    # Connection
    # ----------------
    async def connect(self) -> None:
        async with self._connect_lock:
            if self._running and self._redis is not None:
                return
            try:
                self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
                self._pubsub = self._redis.pubsub()
                self._running = True
                logger.info(f"[RedisBus] Connected to {self.redis_url}")
            except Exception as e:
                logger.exception(f"[RedisBus] Failed to connect: {e}")
                raise

    async def close(self) -> None:
        """Stops listener and closes redis connection."""
        try:
            self._running = False
            if self._listener_task:
                self._listener_task.cancel()
                try:
                    await self._listener_task
                except asyncio.CancelledError:
                    pass
            if self._pubsub:
                try:
                    await self._pubsub.close()
                except Exception:
                    pass
                self._pubsub = None
            if self._redis:
                try:
                    await self._redis.close()
                except Exception:
                    pass
                self._redis = None
            logger.info("[RedisBus] Closed")
        except Exception as e:
            logger.exception(f"[RedisBus] Error during close: {e}")
            raise

    # ----------------
    # Publish
    # ----------------
    async def publish(self, topic: str, message: Any) -> None:
        """Publish JSON message. Lazy-connect if necessary."""
        channel = topic
        if not self._redis:
            await self.connect()
        try:
            payload = json.dumps(message)
            await self._redis.publish(channel, payload)
            logger.debug(f"[RedisBus] Published to {channel}: {message}")
        except Exception as e:
            logger.exception(f"[RedisBus] Publish error on {channel}: {e}")
            raise

    # ----------------
    # Subscribe / Unsubscribe
    # ----------------
    async def subscribe(self, topic: str, callback: Callable[[dict], Any]) -> None:
        """
        Register callback for channel.
        Callback can be an async function or a sync function.
        """
        channel = topic
        if not self._pubsub or not self._redis:
            await self.connect()
        # add callback
        callbacks = self._callbacks.setdefault(channel, [])
        callbacks.append(callback)

        # ensure subscription at server
        await self._pubsub.subscribe(channel)
        logger.info(
            f"[RedisBus] Subscribed to channel: {channel} (callbacks={len(callbacks)})"
        )

        # ensure listener loop is running
        if not self._listener_task or self._listener_task.done():
            self._listener_task = asyncio.create_task(self._listen_loop())

    async def unsubscribe(
        self, topic: str, callback: Optional[Callable] = None
    ) -> None:
        """Remove callback; if none left for channel, unsubscribe from Redis."""
        channel = topic
        if channel not in self._callbacks:
            return
        if callback is None:
            self._callbacks.pop(channel, None)
        else:
            try:
                self._callbacks[channel].remove(callback)
            except ValueError:
                pass
            if not self._callbacks[channel]:
                self._callbacks.pop(channel, None)

        # if no callbacks for channel -> unsubscribe from redis
        if channel not in self._callbacks and self._pubsub:
            try:
                await self._pubsub.unsubscribe(channel)
                logger.info(f"[RedisBus] Unsubscribed from channel: {channel}")
            except Exception:
                logger.exception(
                    f"[RedisBus] Error unsubscribing from channel: {channel}"
                )

    # ----------------
    # Listener loop
    # ----------------
    async def _listen_loop(self):
        """Background loop that reads pubsub messages and dispatches to callbacks."""
        logger.info("[RedisBus] Listener loop started")
        try:
            while self._running and self._pubsub:
                try:
                    message = await self._pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if not message:
                        await asyncio.sleep(0.01)
                        continue

                    channel = message.get("channel")
                    raw = message.get("data")
                    payload = None
                    try:
                        if isinstance(raw, (str, bytes)):
                            payload = json.loads(raw)
                        else:
                            payload = raw
                    except Exception:
                        # fallback - put raw in dict
                        payload = {"raw": raw}

                    # dispatch to registered callbacks (copy list)
                    cbs = list(self._callbacks.get(channel, []))
                    for cb in cbs:
                        try:
                            # async callback
                            if asyncio.iscoroutinefunction(cb):
                                asyncio.create_task(cb(payload))
                            else:
                                # run sync callback in default executor to avoid blocking loop
                                loop = asyncio.get_running_loop()
                                loop.run_in_executor(None, cb, payload)
                        except Exception as cb_err:
                            logger.exception(
                                f"[RedisBus] Error dispatching to callback for {channel}: {cb_err}"
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.exception(f"[RedisBus] Listener loop error: {e}")
                    # backoff a bit on errors before retrying
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("[RedisBus] Listener cancelled")
        finally:
            logger.info("[RedisBus] Listener loop exiting")
