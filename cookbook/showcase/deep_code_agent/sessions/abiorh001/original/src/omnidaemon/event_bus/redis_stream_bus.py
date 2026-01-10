import asyncio
import json
import logging
import time
from typing import Optional, Callable, Dict, Any
from redis import asyncio as aioredis
from decouple import config

logger = logging.getLogger("redis_stream_bus")
logger.setLevel(logging.INFO)


class RedisStreamEventBus:
    """
    Redis Streams Event Bus (production-ready):
    - Uses Redis Streams for durable, reliable messaging.
    - Supports consumer groups for load balancing and fault tolerance.
    - Implements message reclaiming and dead-letter queues (DLQ).
    - Emits monitoring metrics to a dedicated stream.
    """

    def __init__(
        self,
        redis_url: str = config("REDIS_URL"),
        default_maxlen: int = 10_000,
        reclaim_interval: int = 30,
        default_reclaim_idle_ms: int = 180_000,
        default_dlq_retry_limit: int = 3,
    ):
        self.redis_url = redis_url
        self.default_maxlen = default_maxlen
        self.reclaim_interval = reclaim_interval
        self.default_reclaim_idle_ms = default_reclaim_idle_ms
        self.default_dlq_retry_limit = default_dlq_retry_limit

        self._redis: Optional[aioredis.Redis] = None
        self._connect_lock = asyncio.Lock()

        self._consumers: Dict[str, Dict[str, Any]] = {}
        self._in_flight: Dict[str, set] = {}
        self._running = False

    async def connect(self):
        async with self._connect_lock:
            if self._redis:
                return
            self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
            logger.info(f"[RedisStreamBus] connected: {self.redis_url}")

    async def close(self):
        self._running = False
        # cancel consumer tasks
        for meta in list(self._consumers.values()):
            t = meta.get("task")
            r = meta.get("reclaim_task")
            if t:
                t.cancel()
            if r:
                r.cancel()

        if self._redis:
            await self._redis.close()
            self._redis = None
        logger.info("[RedisStreamBus] closed")

    async def publish(
        self, event_payload: Dict[str, Any], maxlen: Optional[int] = None
    ) -> None:
        """
        Publish message to omni-stream:{topic}.
        Returns the assigned stream id.
        """
        if not self._redis:
            await self.connect()
        topic = event_payload.get("topic")
        if not topic:
            raise ValueError("Event payload must include 'topic' field")
        data = {
            "content": event_payload.get("payload", {}).get("content"),
            "webhook": event_payload.get("payload", {}).get("webhook"),
            "topic": topic,
            "task_id": event_payload.get("id"),
            "delivery_attempts": event_payload.get("delivery_attempts", 0),
            "created_at": event_payload.get("created_at", time.time()),
        }
        stream_name = f"omni-stream:{topic}"
        payload = json.dumps(data, default=str)
        maxlen = maxlen or self.default_maxlen
        msg_id = await self._redis.xadd(
            stream_name, {"data": payload}, maxlen=maxlen, approximate=True
        )
        logger.debug(f"[RedisStreamBus] published {stream_name} id={msg_id}")

    async def subscribe(
        self,
        topic: str,
        agent_name: str,
        callback: Callable[[dict], Any],
        group_name: Optional[str] = None,
        consumer_name: Optional[str] = None,
        config: Dict[str, Any] = {},
    ):
        """
        Subscribe a callback to topic. Default behavior creates a unique group
        per subscription (fan-out).
        """
        if not self._redis:
            await self.connect()
        reclaim_idle_ms = config.get("reclaim_idle_ms")
        dlq_retry_limit = config.get("dlq_retry_limit")
        consumer_count = int(config.get("consumer_count", 1))
        effective_reclaim_idle_ms = reclaim_idle_ms or self.default_reclaim_idle_ms
        effective_dlq_retry_limit = dlq_retry_limit or self.default_dlq_retry_limit
        stream_name = f"omni-stream:{topic}"

        group = group_name or (f"group:{topic}:{agent_name}")
        consumer = consumer_name or f"consumer:{agent_name}"
        dlq_stream = f"omni-dlq:{group}"
        # create group if not exists
        try:
            await self._redis.xgroup_create(stream_name, group, id="$", mkstream=True)
            logger.info(f"[RedisStreamBus] created group {group} for {stream_name}")
        except aioredis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.debug(f"[RedisStreamBus] group {group} exists")
            else:
                raise

        self._running = True
        consume_tasks = []
        reclaim_tasks = []
        for i in range(consumer_count):
            consumer_name = f"{consumer}-{i + 1}"
            consume_task = asyncio.create_task(
                self._consume_loop(
                    stream_name=stream_name,
                    topic=topic,
                    group=group,
                    consumer=consumer_name,
                    callback=callback,
                )
            )
            reclaim_task = asyncio.create_task(
                self._reclaim_loop(
                    stream_name=stream_name,
                    topic=topic,
                    group=group,
                    consumer=consumer_name,
                    callback=callback,
                    reclaim_idle_ms=effective_reclaim_idle_ms,
                    dlq_retry_limit=effective_dlq_retry_limit,
                )
            )
            consume_tasks.append(consume_task)
            reclaim_tasks.append(reclaim_task)
        self._consumers[group] = {
            "topic": topic,
            "agent_name": agent_name,
            "stream": stream_name,
            "callback": callback,
            "group": group,
            "dlq": dlq_stream,
            "config": {
                "reclaim_idle_ms": effective_reclaim_idle_ms,
                "dlq_retry_limit": effective_dlq_retry_limit,
                "consumer_count": consumer_count,
            },
            "consume_tasks": consume_tasks,
            "reclaim_tasks": reclaim_tasks,
        }

        logger.info(
            f"[RedisStreamBus] subscribed topic={topic} group={group} consumers={consumer_count}"
        )

    async def _consume_loop(
        self,
        stream_name: str,
        topic: str,
        group: str,
        consumer: str,
        callback: Callable,
    ):
        """
        Loop reading from group (XREADGROUP) and calling callback.
        On failure the message is left pending entries list for reclaim.
        """
        logger.info(
            f"[RedisStreamBus] consumer loop start topic={topic} group={group} consumer={consumer}"
        )
        try:
            while self._running:
                try:
                    entries = await self._redis.xreadgroup(
                        groupname=group,
                        consumername=consumer,
                        streams={stream_name: ">"},
                        count=10,
                        block=5000,
                    )
                    if not entries:
                        continue
                    for _, msgs in entries:
                        for msg_id, fields in msgs:
                            raw = fields.get("data")
                            try:
                                payload = json.loads(raw)
                            except Exception:
                                payload = {"raw": raw}
                            # Mark as in-flight this will help us prevent duplicate processing if the idle time kicks in during processing to avoid the reclaim loop picking it up
                            if group not in self._in_flight:
                                self._in_flight[group] = set()
                            self._in_flight[group].add(msg_id)
                            try:
                                # lets add the consumer name to process the event to the payload just for logging
                                payload["processing_consumer"] = consumer
                                if asyncio.iscoroutinefunction(callback):
                                    await callback(payload)
                                else:
                                    loop = asyncio.get_running_loop()
                                    await loop.run_in_executor(None, callback, payload)
                                await self._redis.xack(stream_name, group, msg_id)
                                # publish monitor metric
                                await self._emit_monitor(
                                    {
                                        "topic": topic,
                                        "event": "processed",
                                        "msg_id": msg_id,
                                        "group": group,
                                        "consumer": consumer,
                                        "timestamp": time.time(),
                                    }
                                )
                            except Exception as cb_err:
                                logger.exception(
                                    f"[RedisStreamBus] callback error topic={topic} id={msg_id}: {cb_err}"
                                )
                            finally:
                                # either success or failure, remove from in-flight so it can either be reclaimed
                                self._in_flight[group].discard(msg_id)

                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    logger.exception(
                        f"[RedisStreamBus] error in consume loop topic={topic}: {err}"
                    )
                    await asyncio.sleep(1)
        finally:
            logger.info(f"[RedisStreamBus] consumer loop stopped topic={topic}")

    async def _reclaim_loop(
        self,
        stream_name: str,
        topic: str,
        group: str,
        consumer: str,
        callback: Callable,
        reclaim_idle_ms: Optional[int] = None,
        dlq_retry_limit: Optional[int] = None,
    ):
        logger.info(f"[RedisStreamBus] reclaim loop start topic={topic} group={group}")
        retry_key = f"retry_counts:{group}"
        reclaim_idle_ms = reclaim_idle_ms or self.default_reclaim_idle_ms
        dlq_retry_limit = dlq_retry_limit or self.default_dlq_retry_limit

        while self._running:
            try:
                pending = []
                try:
                    pending = await self._redis.xpending_range(
                        stream_name, group, "-", "+", count=50
                    )
                except Exception:
                    logger.debug(
                        f"[RedisStreamBus] xpending_range unavailable for {group}, skipping reclaim"
                    )
                    await asyncio.sleep(self.reclaim_interval)
                    continue

                for entry in pending:
                    try:
                        if isinstance(entry, dict):
                            msg_id = entry.get("message_id") or entry.get("id")
                            idle = entry.get("time_since_delivered", 0)
                        elif isinstance(entry, (tuple, list)):
                            msg_id = entry[0]
                            idle = int(entry[2]) if len(entry) > 2 else 0
                        else:
                            continue

                        if not msg_id or idle < reclaim_idle_ms:
                            continue
                        # the task is still being processed by the consumer, skip reclaim to avoid duplicate processing
                        if (
                            group in self._in_flight
                            and msg_id in self._in_flight[group]
                        ):
                            # logger.debug(
                            #     f"[RedisStreamBus] Skipping reclaim of {msg_id}: still in-flight"
                            # )
                            print(
                                f"[RedisStreamBus] Skipping reclaim of {msg_id}: still in-flight"
                            )
                            continue
                        print(
                            f"consumer group {group} reclaiming message id {msg_id} meant for topic {topic}"
                        )

                        claimed = await self._redis.xclaim(
                            stream_name,
                            group,
                            consumer,
                            min_idle_time=reclaim_idle_ms,
                            message_ids=[msg_id],
                        )

                        if not claimed:
                            continue

                        # logger.info(
                        #     f"[RedisStreamBus] reclaimed {msg_id} (idle={idle}ms) for group {group}"
                        # )
                        print(
                            f"[RedisStreamBus] reclaimed {msg_id} (idle={idle}ms) for group {group}"
                        )
                        await self._emit_monitor(
                            {
                                "topic": topic,
                                "event": "reclaim_attempt",
                                "msg_id": msg_id,
                                "group": group,
                                "consumer": consumer,
                                "timestamp": time.time(),
                            }
                        )

                        for msg in claimed:
                            _id = msg[0]
                            fields = msg[1]
                            raw = fields.get("data")
                            try:
                                payload = json.loads(raw) if raw else {"raw": raw}
                            except Exception:
                                payload = {"raw": raw}

                            retry_count = await self._redis.hincrby(retry_key, _id, 1)
                            await self._redis.expire(retry_key, 3600)
                            retry_count = int(retry_count)

                            if retry_count > dlq_retry_limit:
                                logger.error(
                                    f"[RedisStreamBus] Max delivery attempts ({1 + dlq_retry_limit}) exceeded for {_id} after {dlq_retry_limit} retries. Sending to DLQ."
                                )
                                # lets ensure we update the payload with the delivery attempts before sending to DLQ, but first adjust because we incremented before checking
                                retry_count -= 1
                                payload["delivery_attempts"] += retry_count
                                await self._send_to_dlq(
                                    group,
                                    stream_name,
                                    _id,
                                    payload,
                                    error=f"Max retries ({1 + dlq_retry_limit}) exceeded",
                                    retry_count=retry_count,
                                )
                                await self._redis.xack(stream_name, group, _id)
                                await self._redis.hdel(retry_key, _id)
                                await self._emit_monitor(
                                    {
                                        "topic": topic,
                                        "event": "dlq_push",
                                        "msg_id": _id,
                                        "group": group,
                                        "consumer": consumer,
                                        "timestamp": time.time(),
                                    }
                                )
                            else:
                                try:
                                    logger.info(
                                        f"[RedisStreamBus] Retry #{retry_count} for {_id} in group {group}"
                                    )
                                    # increment the delivery attempts in the payload
                                    payload["delivery_attempts"] += retry_count
                                    # lets add the consumer name to process the event to the payload just for logging
                                    payload["processing_consumer"] = consumer
                                    if asyncio.iscoroutinefunction(callback):
                                        await callback(payload)
                                    else:
                                        loop = asyncio.get_running_loop()
                                        await loop.run_in_executor(
                                            None, callback, payload
                                        )
                                    await self._redis.xack(stream_name, group, _id)
                                    await self._redis.hdel(retry_key, _id)
                                    await self._emit_monitor(
                                        {
                                            "topic": topic,
                                            "event": "reclaimed",
                                            "msg_id": _id,
                                            "group": group,
                                            "consumer": consumer,
                                            "timestamp": time.time(),
                                        }
                                    )
                                except Exception as err2:
                                    logger.exception(
                                        f"[RedisStreamBus] Retry #{retry_count} failed for {_id}: {err2}"
                                    )

                    except Exception as e:
                        logger.exception(
                            f"[RedisStreamBus] reclaim entry handling error: {e}"
                        )

                await asyncio.sleep(self.reclaim_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"[RedisStreamBus] reclaim loop error: {e}")
                await asyncio.sleep(self.reclaim_interval)

        logger.info(f"[RedisStreamBus] reclaim loop stopped topic={topic}")

    async def _send_to_dlq(
        self,
        group: str,
        stream_name: str,
        msg_id: str,
        payload: dict,
        error: str,
        retry_count: int,
    ):
        """Send message to per-group DLQ."""
        dlq_stream = f"omni-dlq:{group}"
        dlq_payload = {
            "topic": stream_name.replace("omni-stream:", ""),
            "original_stream": stream_name,
            "original_id": msg_id,
            "failed_message": payload,
            "error": error,
            "retry_count": retry_count,
            "failed_at": time.time(),
        }
        try:
            await self._redis.xadd(
                dlq_stream,
                {"data": json.dumps(dlq_payload, default=str)},
                maxlen=self.default_maxlen,
                approximate=True,
            )
            logger.info(f"[RedisStreamBus] Sent {msg_id} to DLQ: {dlq_stream}")
        except Exception as e:
            logger.critical(
                f"[RedisStreamBus] FAILED to write to DLQ {dlq_stream}: {e}"
            )

    async def _emit_monitor(self, metric: dict):
        """
        Emit metric to a durable Redis Stream: omni-metrics.
        This is the single source of truth for all events.
        """
        try:
            # Write to metrics stream
            await self._redis.xadd(
                "omni-metrics",
                {"data": json.dumps(metric, default=str)},
                maxlen=1_000_000,
                approximate=True,
            )
        except Exception as e:
            logger.error(f"[RedisStreamBus] Failed to emit metric: {e}")

    async def unsubscribe(
        self,
        topic: str,
        agent_name: str,
        delete_group: bool = False,
        delete_dlq: bool = False,
    ):
        """
        Unsubscribe an agent from a topic by stopping its consumer group.
        If delete_group is True, the consumer group is deleted permanently.
        If delete_dlq is True, the associated DLQ is also deleted.
        """
        if not self._redis:
            await self.connect()
        group_name = f"group:{topic}:{agent_name}"

        stream_name = f"omni-stream:{topic}"
        dlq_name = f"omni-dlq:{group_name}"

        meta = self._consumers.get(group_name)
        if not meta:
            return

        # Cancel all tasks
        for task in meta["consume_tasks"]:
            task.cancel()
        for task in meta["reclaim_tasks"]:
            task.cancel()

        # Remove from registry
        del self._consumers[group_name]

        logger.info(f"[RedisStreamBus] Stopped consumption for {group_name}")

        if delete_group:
            try:
                await self._redis.xgroup_destroy(stream_name, group_name)
                logger.info(f"[RedisStreamBus] Deleted consumer group {group_name}")
            except Exception as e:
                if "NOGROUP" not in str(e):
                    logger.error(f"Failed to delete group: {e}")

        if delete_dlq:
            try:
                await self._redis.delete(dlq_name)
                logger.info(f"[RedisStreamBus] Deleted DLQ {dlq_name}")
            except Exception as e:
                logger.warning(f"Failed to delete DLQ: {e}")
        else:
            logger.info(f"[RedisStreamBus] DLQ preserved at {dlq_name}")

    async def get_consumers(self) -> Dict[str, Any]:
        """Return current consumers and their configurations."""
        return self._consumers
