import json
import uuid
from typing import Any, List, Callable
import redis.asyncio as redis
import threading
import asyncio

from omnicoreagent.core.budgets import (
    counters_view,
    legacy_budget_parts,
)
from omnicoreagent.core.memory_store.base import AbstractMemoryStore
from omnicoreagent.core.logging import logger
from omnicoreagent.core.summarizer.summarizer_engine import (
    apply_summarization_logic,
)
from omnicoreagent.core.summarizer.summarizer_types import SummaryConfig
from datetime import datetime, timezone



class RedisConnectionManager:
    """
    Redis connection manager for efficient connection pooling and reuse.
    """

    def __init__(self, redis_url: str):
        # One manager per store and URL: two stores with different URLs must
        # never share a client.
        self.redis_url = redis_url
        self._lock = threading.RLock()
        self._client = None
        self._connection_count = 0
        logger.debug("RedisConnectionManager initialized")

    async def get_client(self) -> redis.Redis:
        """Get or create Redis client with connection pooling."""
        with self._lock:
            if self._client is None:
                try:
                    # A blocking pool: past 20 connections a caller waits its
                    # turn. The plain pool raised MaxConnectionsError instead,
                    # and 200 runs charging one budget at once (each a tiny
                    # script) failed 180 of them on it.
                    pool = redis.BlockingConnectionPool.from_url(
                        self.redis_url,
                        decode_responses=True,
                        max_connections=20,
                        timeout=30,
                        retry_on_timeout=True,
                        socket_timeout=5,
                        socket_connect_timeout=5,
                        health_check_interval=30,
                    )
                    self._client = redis.Redis.from_pool(pool)
                    logger.debug(
                        "[RedisManager] Created Redis connection pool"
                    )
                except Exception as e:
                    logger.error(f"[RedisManager] Failed to create Redis client: {e}")
                    raise

            self._connection_count += 1
            logger.debug(
                f"[RedisManager] Redis connection usage count: {self._connection_count}"
            )
            return self._client

    def release_client(self):
        """Release a Redis client (decrement usage count)."""
        with self._lock:
            if self._connection_count > 0:
                self._connection_count -= 1
                logger.debug(
                    f"[RedisManager] Redis connection usage count: {self._connection_count}"
                )

    async def close_all(self):
        """Close all Redis connections."""
        with self._lock:
            if self._client:
                await self._client.close()
                self._client = None
                self._connection_count = 0
                logger.debug("[RedisManager] Closed all Redis connections")





class RedisMemoryStore(AbstractMemoryStore):
    """Redis-backed memory store implementing AbstractMemoryStore interface."""

    def __init__(
        self,
        redis_url: str = None,
    ) -> None:
        """Initialize Redis memory store.

        Args:
            redis_url: Redis connection URL. If None, Redis will not be initialized.
        """
        # Counters left in the 0.5.x shape are looked for once per key.
        self._legacy_budgets_moved: set[str] = set()
        if redis_url is None:
            logger.debug("RedisMemoryStore skipped - redis_url not provided")
            self._connection_manager = None
            self._redis_client = None
            self.memory_config: dict[str, Any] = {}
            return

        self._connection_manager = RedisConnectionManager(redis_url)
        self._redis_client = None
        self.memory_config: dict[str, Any] = {}
        self.summary_config: dict[str, Any] = {}
        self.summarize_fn: Callable = None
        logger.debug(f"Initialized RedisMemoryStore with redis_url: {redis_url}")

    async def _get_client(self) -> redis.Redis:
        """Get Redis client from connection manager or direct client."""
        if self._connection_manager:
            return await self._connection_manager.get_client()
        elif self._redis_client:
            return self._redis_client
        else:
            raise RuntimeError("Redis not configured - REDIS_URL not set")

    async def _scan_keys(self, client: redis.Redis, match: str) -> List[str]:
        """Scan keys using non-blocking SCAN command."""
        keys = []
        async for key in client.scan_iter(match=match):
            keys.append(key)
        return keys

    def set_memory_config(
        self,
        mode: str,
        value: int = None,
        summary_config: dict = None,
        summarize_fn: Callable = None,
    ) -> None:
        valid_modes = {"sliding_window", "token_budget"}
        if mode.lower() not in valid_modes:
            raise ValueError(
                f"Invalid memory mode: {mode}. Must be one of {valid_modes}."
            )
        self.memory_config = {"mode": mode, "value": value}
        if summary_config:
            self.summary_config = SummaryConfig(**summary_config)
        if summarize_fn:
            self.summarize_fn = summarize_fn

    async def store_message(
        self,
        role: str,
        content: str,
        metadata: dict | None = None,
        session_id: str = None,
    ) -> None:
        """Store a message in Redis.

        Args:
            role: Message role (e.g., 'user', 'assistant')
            content: Message content
            metadata: Optional metadata about the message
            session_id: Session ID for grouping messages
        """
        client = None
        try:
            client = await self._get_client()
            metadata = metadata or {}

            key = f"omnicoreagent_memory:{session_id}"

            dt = datetime.now(timezone.utc)
            timestamp_iso = dt.isoformat()
            timestamp_score = dt.timestamp()

            message = {
                "id": str(uuid.uuid4()),
                "role": role,
                "content": str(content),
                "session_id": session_id,
                "msg_metadata": metadata,
                "timestamp": timestamp_iso,
                "status": "active",
                "inactive_reason": None,
                "summary_id": None,
            }

            await client.zadd(key, {json.dumps(message): timestamp_score})
            logger.debug(f"Stored message for session {session_id}")

        except Exception as e:
            # Raised, not swallowed: a message that was never stored must not
            # look stored (found merging the P6 tracks, 2026-10-07).
            logger.error(f"Failed to store message: {e}")
            raise
        finally:
            if self._connection_manager and client:
                self._connection_manager.release_client()

    async def get_messages(
        self, session_id: str = None, agent_name: str = None
    ) -> List[dict]:
        """Get messages from Redis.

        Args:
            session_id: Session ID to get messages for
            agent_name: Optional agent name filter

        Returns:
            List of messages
        """
        client = None
        try:
            client = await self._get_client()
            key = f"omnicoreagent_memory:{session_id}"

            raw_messages = await client.zrange(key, 0, -1)

            if not raw_messages:
                return []

            result = []
            for msg_json in raw_messages:
                try:
                    msg = json.loads(msg_json)
                    if msg.get("status", "active") != "active":
                        continue
                    if (
                        agent_name
                        and msg.get("msg_metadata", {}).get("agent_name") != agent_name
                    ):
                        continue
                    result.append(msg)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse message JSON: {msg_json}")
                    continue

            result, summary_msg, summarized_ids = await apply_summarization_logic(
                messages=result,
                memory_config=self.memory_config,
                summary_config=self.summary_config,
                summarize_fn=self.summarize_fn,
                agent_name=agent_name,
            )

            if summarized_ids and summary_msg:
                summary_id = str(uuid.uuid4())
                summary_msg["id"] = summary_id

                dt = datetime.now(timezone.utc)
                timestamp_iso = dt.isoformat()
                timestamp_score = dt.timestamp()

                summary_redis_msg = {
                    "id": summary_id,
                    "role": summary_msg["role"],
                    "content": summary_msg["content"],
                    "session_id": session_id,
                    "msg_metadata": summary_msg["msg_metadata"],
                    "timestamp": timestamp_iso,
                    "status": "active",
                    "inactive_reason": None,
                    "summary_id": None,
                }

                async def _background_persist_summary():
                    client = None
                    try:
                        client = await self._get_client()
                        key = f"omnicoreagent_memory:{session_id}"

                        await client.zadd(
                            key, {json.dumps(summary_redis_msg): timestamp_score}
                        )

                        await self.mark_messages_summarized(
                            message_ids=summarized_ids,
                            summary_id=summary_id,
                            retention_policy=getattr(
                                self.summary_config.retention_policy,
                                "value",
                                self.summary_config.retention_policy,
                            ),
                        )
                    except Exception as e:
                        logger.error(f"Background Redis persistence failed: {e}")
                    finally:
                        if self._connection_manager and client:
                            self._connection_manager.release_client()

                asyncio.create_task(_background_persist_summary())

            return result

        except Exception as e:
            logger.error(f"Failed to get messages: {e}")
            return []
        finally:
            if self._connection_manager and client:
                self._connection_manager.release_client()

    async def clear_memory(
        self, session_id: str = None, agent_name: str = None
    ) -> None:
        """Clear memory in Redis efficiently.

        Args:
            session_id: Specific session to clear (if None, all sessions)
            agent_name: Specific agent to clear (if None, all messages)
        """
        client = None
        try:
            client = await self._get_client()

            if session_id and agent_name:
                await self._clear_agent_from_session(client, session_id, agent_name)

            elif session_id:
                key = f"omnicoreagent_memory:{session_id}"
                await client.delete(key)
                logger.debug(f"Cleared all memory for session {session_id}")

            elif agent_name:
                await self._clear_agent_across_sessions(client, agent_name)

            else:
                pattern = "omnicoreagent_memory:*"
                keys = await self._scan_keys(client, pattern)
                if keys:
                    await client.delete(*keys)
                    logger.debug(f"Cleared all memory ({len(keys)} sessions)")

        except Exception as e:
            logger.error(f"Failed to clear memory: {e}")
        finally:
            if self._connection_manager and client:
                self._connection_manager.release_client()

    async def _clear_agent_from_session(
        self, client: redis.Redis, session_id: str, agent_name: str
    ) -> None:
        """Clear messages for a specific agent from a session efficiently."""
        key = f"omnicoreagent_memory:{session_id}"
        messages = await client.zrange(key, 0, -1)

        if not messages:
            logger.debug(f"No messages found for session {session_id}")
            return

        to_remove = []
        for msg_json in messages:
            try:
                msg_data = json.loads(msg_json)
                if msg_data.get("msg_metadata", {}).get("agent_name") == agent_name:
                    to_remove.append(msg_json)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse message JSON: {msg_json}")
                continue

        if to_remove:
            async with client.pipeline(transaction=False) as pipe:
                for msg in to_remove:
                    pipe.zrem(key, msg)
                await pipe.execute()
            logger.debug(
                f"Cleared {len(to_remove)} messages for agent {agent_name} in session {session_id}"
            )
        else:
            logger.debug(
                f"No messages found for agent {agent_name} in session {session_id}"
            )

    async def _clear_agent_across_sessions(
        self, client: redis.Redis, agent_name: str
    ) -> None:
        """Clear messages for a specific agent across all sessions efficiently."""
        pattern = "omnicoreagent_memory:*"
        keys = await self._scan_keys(client, pattern)

        if not keys:
            logger.debug("No session keys found")
            return

        total_removed = 0

        for key in keys:
            messages = await client.zrange(key, 0, -1)
            if not messages:
                continue

            to_remove = []
            for msg_json in messages:
                try:
                    msg_data = json.loads(msg_json)
                    if msg_data.get("msg_metadata", {}).get("agent_name") == agent_name:
                        to_remove.append(msg_json)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse message JSON in {key}: {msg_json}")
                    continue

            if to_remove:
                async with client.pipeline(transaction=False) as pipe:
                    for msg in to_remove:
                        pipe.zrem(key, msg)
                    await pipe.execute()

                total_removed += len(to_remove)
                logger.debug(
                    f"Cleared {len(to_remove)} messages for agent {agent_name} in {key}"
                )

        logger.debug(
            f"Cleared {total_removed} messages for agent {agent_name} across all sessions"
        )

    def _serialize(self, data: Any) -> str:
        """Convert any non-serializable data into a JSON-compatible format."""
        try:
            return json.dumps(data, default=lambda o: o.__dict__)
        except Exception as e:
            logger.error(f"Serialization failed: {e}")
            return json.dumps({"error": "Serialization failed"})

    def _deserialize(self, data: Any) -> Any:
        """Convert stored JSON strings back to their original format if needed."""
        try:
            if "msg_metadata" in data:
                data["msg_metadata"] = json.loads(data["msg_metadata"])
            return data
        except Exception as e:
            logger.error(f"Deserialization failed: {e}")
            return data

    async def mark_messages_summarized(
        self,
        message_ids: list[str],
        summary_id: str,
        retention_policy: str = "keep",
    ) -> None:
        """Mark messages as summarized (inactive or delete based on policy).

        Args:
            message_ids: List of message IDs to mark as summarized
            summary_id: ID of the summary message that replaces these
            retention_policy: 'keep' to mark inactive, 'delete' to remove
        """
        if not message_ids:
            return

        client = None
        message_ids_set = set(message_ids)

        try:
            client = await self._get_client()
            pattern = "omnicoreagent_memory:*"
            keys = await self._scan_keys(client, pattern)

            for key in keys:
                raw_messages = await client.zrange(key, 0, -1, withscores=True)
                if not raw_messages:
                    continue

                to_remove = []
                to_add = []

                for msg_json, score in raw_messages:
                    try:
                        msg = json.loads(msg_json)
                        if msg.get("id") in message_ids_set:
                            if retention_policy == "delete":
                                to_remove.append(msg_json)
                            else:
                                to_remove.append(msg_json)
                                msg["status"] = "inactive"
                                msg["inactive_reason"] = "summarized"
                                msg["summary_id"] = summary_id
                                to_add.append((json.dumps(msg), score))
                    except json.JSONDecodeError:
                        continue

                if to_remove or to_add:
                    async with client.pipeline(transaction=False) as pipe:
                        for msg_json in to_remove:
                            pipe.zrem(key, msg_json)
                        for msg_json, score in to_add:
                            pipe.zadd(key, {msg_json: score})
                        await pipe.execute()

            logger.debug(
                f"{'Deleted' if retention_policy == 'delete' else 'Marked inactive'} "
                f"{len(message_ids)} summarized messages in Redis"
            )

        except Exception as e:
            logger.error(f"Failed to mark messages as summarized: {e}")
        finally:
            if self._connection_manager and client:
                self._connection_manager.release_client()

    # --- run state ---------------------------------------------------------
    # One hash per run (version, session, status, data) and one set of run IDs
    # per session. Saves are compare-and-swap in a Lua script, so they are
    # atomic on the server.

    _SAVE_RUN = """
    local current = redis.call('HGET', KEYS[1], 'version')
    if ARGV[1] == '' then
        if current then return 0 end
    elseif current ~= ARGV[1] then
        return 0
    end
    redis.call('HSET', KEYS[1], 'version', ARGV[2], 'session_id', ARGV[3],
               'status', ARGV[4], 'data', ARGV[5])
    if ARGV[3] ~= '' then redis.call('SADD', KEYS[2], ARGV[6]) end
    redis.call('SADD', KEYS[3], ARGV[6])
    return 1
    """

    async def save_run_state(self, record: dict, expected_version: int | None) -> int:
        from omnicoreagent.core.runs import RunStateConflict

        client = await self._get_client()
        run_id = record["run_id"]
        session_id = record.get("session_id") or ""
        version = (expected_version or 0) + 1
        data = json.dumps({**record, "version": version}, default=str)
        saved = await client.eval(
            self._SAVE_RUN,
            3,
            f"omnicoreagent_run:{run_id}",
            f"omnicoreagent_runs:session:{session_id}",
            "omnicoreagent_runs:all",
            "" if expected_version is None else str(expected_version),
            str(version),
            session_id,
            record.get("status", "running"),
            data,
            run_id,
        )
        if not saved:
            reason = "already exists" if expected_version is None else f"changed since version {expected_version}"
            raise RunStateConflict(f"Run {run_id} {reason}")
        return version

    async def get_run_state(self, run_id: str) -> dict | None:
        client = await self._get_client()
        data = await client.hget(f"omnicoreagent_run:{run_id}", "data")
        return json.loads(data) if data else None

    async def list_run_states(
        self, session_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict]:
        client = await self._get_client()
        key = (
            f"omnicoreagent_runs:session:{session_id}"
            if session_id is not None
            else "omnicoreagent_runs:all"
        )
        records = []
        for run_id in await client.smembers(key):
            record = await self.get_run_state(run_id)
            if record is not None and (status is None or record.get("status") == status):
                records.append(record)
        return sorted(records, key=lambda r: r.get("created_at") or "")[:limit]

    async def delete_finished_run_states(self, *, before: str, statuses: tuple[str, ...]) -> int:
        client = await self._get_client()
        removed = 0
        # SSCAN, not SMEMBERS: a long-lived store holds many runs.
        async for run_id in client.sscan_iter("omnicoreagent_runs:all"):
            key = f"omnicoreagent_run:{run_id}"
            status, session_id, data = await client.hmget(key, "status", "session_id", "data")
            if data is None:
                await client.srem("omnicoreagent_runs:all", run_id)
                continue
            if status not in statuses:
                continue
            if (json.loads(data).get("created_at") or "") >= before:
                continue
            await client.delete(key)
            await client.srem("omnicoreagent_runs:all", run_id)
            if session_id:
                await client.srem(f"omnicoreagent_runs:session:{session_id}", run_id)
            removed += 1
        return removed

    # --- budgets -----------------------------------------------------------
    # Per budget key: a hash of ``<meter>:spent|reserved|granted`` counters, a
    # hash of holds (one JSON record each), and a list of grants. A change is
    # one Lua script, which Redis runs without interleaving anything else: the
    # limit check and the increments are one step, so a crowd of runs on one
    # key neither passes the limit nor conflicts with itself (the support desk
    # ramp, 2026-10-07). The three keys share a hash tag so a cluster keeps
    # them on one node.

    _APPLY_BUDGET = """
    local meters, holds, grants = KEYS[1], KEYS[2], KEYS[3]
    local c = cjson.decode(ARGV[1])
    local null = cjson.null
    local function num(v) return tonumber(v) or 0 end
    local function get(m, f) return num(redis.call('HGET', meters, m .. ':' .. f)) end
    local function incr(m, f, v)
        if v ~= 0 then redis.call('HINCRBYFLOAT', meters, m .. ':' .. f, string.format('%.17g', v)) end
    end
    local function exact(v) return string.format('%.17g', v) end

    local checks = {}
    if c.hold and c.hold ~= null and c.hold.amount and c.hold.amount ~= 0 then
        table.insert(checks, {c.hold.meter, c.hold.amount, c.hold.limit})
    end
    for _, g in ipairs(c.guard or {}) do table.insert(checks, {g[1], g[2], g[3]}) end
    for _, chk in ipairs(checks) do
        local limit = chk[3]
        if limit ~= nil and limit ~= null then
            local used, held, granted = get(chk[1], 'spent'), get(chk[1], 'reserved'), get(chk[1], 'granted')
            if used + held + chk[2] > limit + granted then
                if held < 0 then held = 0 end
                return {'refused', chk[1], exact(limit + granted), exact(used), exact(held), exact(chk[2])}
            end
        end
    end

    local touched, order = {}, {}
    local function touch(m) if not touched[m] then touched[m] = true; table.insert(order, m) end end
    local released = 0
    local function drop(id)
        local raw = redis.call('HGET', holds, id)
        if not raw then return false end
        local h = cjson.decode(raw)
        redis.call('HDEL', holds, id)
        incr(h.meter, 'reserved', -h.amount)
        return true
    end

    local s = c.settle
    if s and s ~= null then
        local removed = drop(s.id)
        if s.spend ~= nil and s.spend ~= null and (removed or s.even_if_released) then
            incr(s.meter, 'spent', s.spend)
            touch(s.meter)
        end
    end
    if c.release_runs and #c.release_runs > 0 then
        local wanted = {}
        for _, r in ipairs(c.release_runs) do wanted[r] = true end
        local flat = redis.call('HGETALL', holds)
        for i = 1, #flat, 2 do
            local h = cjson.decode(flat[i + 1])
            if h.run_id ~= nil and h.run_id ~= null and wanted[h.run_id] then
                if drop(flat[i]) then released = released + 1 end
            end
        end
    end
    if c.hold and c.hold ~= null and c.hold.amount and c.hold.amount ~= 0 then
        redis.call('HSET', holds, c.hold.id, ARGV[2])
        incr(c.hold.meter, 'reserved', c.hold.amount)
    end
    for _, g in ipairs(c.guard or {}) do incr(g[1], 'spent', g[2]); touch(g[1]) end
    for _, a in ipairs(c.add or {}) do incr(a[1], 'spent', a[2]); touch(a[1]) end
    if c.grant and c.grant ~= null then
        incr(c.grant.meter, 'granted', c.grant.amount)
        redis.call('RPUSH', grants, ARGV[3])
        redis.call('LTRIM', grants, -20, -1)
    end
    local out = {'ok', tostring(released)}
    for _, m in ipairs(order) do
        table.insert(out, m)
        table.insert(out, redis.call('HGET', meters, m .. ':spent') or '0')
    end
    return out
    """

    # Moves a 0.5.x counter (one JSON document under ``omnicoreagent_budget:``)
    # into the keys above, only if the document is still what was read, and
    # removes it in the same step so two workers cannot both add it.
    _MOVE_LEGACY_BUDGET = """
    if redis.call('HGET', KEYS[1], 'data') ~= ARGV[1] then return 0 end
    redis.call('DEL', KEYS[1])
    local parts = cjson.decode(ARGV[2])
    for field, amount in pairs(parts.counters) do
        if amount ~= 0 then
            redis.call('HINCRBYFLOAT', KEYS[2], field, string.format('%.17g', amount))
        end
    end
    for id, raw in pairs(parts.holds) do redis.call('HSET', KEYS[3], id, raw) end
    for _, raw in ipairs(parts.history) do redis.call('RPUSH', KEYS[4], raw) end
    redis.call('LTRIM', KEYS[4], -20, -1)
    return 1
    """

    @staticmethod
    def _budget_keys(key: str) -> tuple[str, str, str]:
        tag = f"omnicoreagent_budget2:{{{key}}}"
        return f"{tag}:meters", f"{tag}:holds", f"{tag}:grants"

    async def _move_legacy_budget(self, key: str) -> None:
        if key in self._legacy_budgets_moved:
            return
        client = await self._get_client()
        legacy = f"omnicoreagent_budget:{key}"
        for _ in range(5):
            data = await client.hget(legacy, "data")
            if not data:
                break
            parts = legacy_budget_parts(json.loads(data))
            meters, holds, grants = self._budget_keys(key)
            counters = {
                f"{meter}:{name}": amount
                for meter, values in parts["counters"].items()
                for name, amount in values.items()
            }
            moved = await client.eval(
                self._MOVE_LEGACY_BUDGET,
                4,
                legacy,
                meters,
                holds,
                grants,
                data,
                json.dumps(
                    {
                        "counters": counters,
                        "holds": {i: json.dumps(h) for i, h in parts["holds"].items()},
                        "history": [json.dumps(e, default=str) for e in parts["history"]],
                    }
                ),
            )
            if moved:
                break
        if len(self._legacy_budgets_moved) > 10_000:
            self._legacy_budgets_moved.clear()
        self._legacy_budgets_moved.add(key)

    async def delete_budget_state(self, key: str) -> None:
        client = await self._get_client()
        await client.delete(f"omnicoreagent_budget:{key}", *self._budget_keys(key))

    async def get_budget_state(self, key: str) -> dict | None:
        await self._move_legacy_budget(key)
        client = await self._get_client()
        fields = await client.hgetall(self._budget_keys(key)[0])
        counters: dict[str, dict[str, float]] = {}
        for field, value in fields.items():
            field = field.decode() if isinstance(field, bytes) else field
            meter, _, name = field.rpartition(":")
            counters.setdefault(meter, {"spent": 0.0, "reserved": 0.0, "granted": 0.0})[name] = float(
                value
            )
        return counters_view(key, counters)

    async def get_budget_grant_history(self, key: str) -> list[dict]:
        await self._move_legacy_budget(key)
        client = await self._get_client()
        return [json.loads(raw) for raw in await client.lrange(self._budget_keys(key)[2], 0, -1)]

    async def list_budget_holds(self, key: str) -> list[dict]:
        await self._move_legacy_budget(key)
        client = await self._get_client()
        held = await client.hgetall(self._budget_keys(key)[1])
        return [
            {"id": i.decode() if isinstance(i, bytes) else i, **json.loads(raw)}
            for i, raw in held.items()
        ]

    async def apply_budget_change(self, key: str, change: dict) -> dict:
        await self._move_legacy_budget(key)
        client = await self._get_client()
        hold = change.get("hold")
        grant = change.get("grant")
        answer = await client.eval(
            self._APPLY_BUDGET,
            3,
            *self._budget_keys(key),
            json.dumps(change, default=str),
            # Stored as Python wrote them, so no digit is lost in the script.
            json.dumps(
                {k: hold[k] for k in ("meter", "amount", "run_id", "held_at")}, default=str
            )
            if hold
            else "",
            json.dumps(grant, default=str) if grant else "",
        )
        answer = [a.decode() if isinstance(a, bytes) else a for a in answer]
        if answer[0] == "refused":
            _, meter, limit, used, held, requested = answer
            return {
                "refused": {
                    "meter": meter,
                    "limit": float(limit),
                    "used": float(used),
                    "reserved": float(held),
                    "requested": float(requested),
                },
                "totals": {},
                "released": 0,
            }
        totals = {answer[i]: float(answer[i + 1]) for i in range(2, len(answer), 2)}
        return {"refused": None, "totals": totals, "released": int(answer[1])}

    # The 0.5.x counter was one hash per budget (version, data); saves were a
    # compare-and-swap in Lua. Nothing in the runtime writes it any more: this
    # is how a counter in that shape is planted, to be moved on first touch.
    _SAVE_BUDGET = """
    local current = redis.call('HGET', KEYS[1], 'version')
    if ARGV[1] == '' then
        if current then return 0 end
    elseif current ~= ARGV[1] then
        return 0
    end
    redis.call('HSET', KEYS[1], 'version', ARGV[2], 'data', ARGV[3])
    return 1
    """

    async def save_budget_state(self, state: dict, expected_version: int | None) -> int:
        from omnicoreagent.core.runs import RunStateConflict

        client = await self._get_client()
        key = state["key"]
        version = (expected_version or 0) + 1
        saved = await client.eval(
            self._SAVE_BUDGET,
            1,
            f"omnicoreagent_budget:{key}",
            "" if expected_version is None else str(expected_version),
            str(version),
            json.dumps({**state, "version": version}, default=str),
        )
        if not saved:
            raise RunStateConflict(f"Budget {key} changed since version {expected_version}")
        return version

