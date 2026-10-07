from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument, errors, IndexModel
from datetime import datetime
import uuid
import asyncio

from omnicoreagent.core.budgets import (
    _GRANT_HISTORY_KEPT as GRANT_HISTORY_KEPT,
    budget_checks,
    counters_view,
    legacy_budget_parts,
    refusal_for,
)
from omnicoreagent.core.memory_store.base import AbstractMemoryStore
from omnicoreagent.core.logging import logger
from omnicoreagent.core.memory_store.utils import utc_now_str
from omnicoreagent.core.summarizer.summarizer_engine import (
    apply_summarization_logic,
)
from omnicoreagent.core.summarizer.summarizer_types import SummaryConfig
from typing import Callable, Any


# A change that lost a race on a document it had read is made again, a few times.
_BUDGET_UPDATE_TRIES = 12


class MongoDb(AbstractMemoryStore):
    def __init__(self, uri: str, db_name: str, collection: str):
        self.uri = uri
        self.db_name = db_name
        self.collection_name = collection
        self.client: AsyncIOMotorClient | None = None
        self.db = None
        self.collection = None
        self._initialized = False
        self.memory_config: dict[str, Any] = {}
        self.summary_config: dict[str, Any] = {}
        self.summarize_fn: Callable = None
        # Counters left in the 0.5.x shape are looked for once per key.
        self._legacy_budgets_moved: set[str] = set()

    async def _ensure_connected(self):
        """Ensure MongoDB connection is established"""
        if self._initialized:
            return

        try:
            collection_name = self.collection_name
            self.client = AsyncIOMotorClient(self.uri)
            await self.client.admin.command("ping")

            self.db = self.client[self.db_name]
            if collection_name is None:
                logger.warning("No collection name provided, using default name")
                collection_name = f"{self.db_name}_collection_name"
            self.collection = self.db[collection_name]
            logger.debug(f"Using collection: {collection_name}")

            message_indexes = [
                IndexModel([("session_id", 1), ("msg_metadata.agent_name", 1)]),
                IndexModel([("session_id", 1)]),
                IndexModel([("msg_metadata.agent_name", 1)]),
                IndexModel([("timestamp", 1)]),
                IndexModel([("status", 1)]),
            ]
            await self.collection.create_indexes(message_indexes)
            self.run_states = self.db[f"{collection_name}_run_states"]
            self.budget_states = self.db[f"{collection_name}_budget_states"]
            self.budgets = self.db[f"{collection_name}_budgets"]
            await self.run_states.create_indexes(
                [IndexModel([("session_id", 1)]), IndexModel([("status", 1)])]
            )

            self._initialized = True
            logger.debug("Connected to MongoDB")

        except BaseException as e:
            # Close what this attempt opened: a client left open keeps its
            # pool and monitors, and the next attempt opens another.
            await self.close()
            if isinstance(e, errors.ConnectionFailure):
                logger.error(f"Failed to connect to MongoDB: {e}")
                raise RuntimeError(f"Could not connect to MongoDB at {self.uri}.") from e
            raise

    async def close(self) -> None:
        """Close the client and its connections. Used again, the store
        connects anew."""
        client, self.client = self.client, None
        self._initialized = False
        self.db = self.collection = None
        if client is not None:
            client.close()

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
        try:
            await self._ensure_connected()
            if metadata is None:
                metadata = {}
            message = {
                "id": str(uuid.uuid4()),
                "role": role,
                "content": content,
                "msg_metadata": metadata,
                "session_id": session_id,
                "timestamp": utc_now_str(),
                "status": "active",
                "inactive_reason": None,
                "summary_id": None,
            }
            await self.collection.insert_one(message)
        except Exception as e:
            # Raised, not swallowed: a message that was never stored must not
            # look stored (found merging the P6 tracks, 2026-10-07).
            logger.error(f"Failed to store message: {e}")
            raise

    async def get_messages(self, session_id: str = None, agent_name: str = None):
        try:
            await self._ensure_connected()
            query = {"status": {"$ne": "inactive"}}
            if session_id:
                query["session_id"] = session_id
            if agent_name:
                query["msg_metadata.agent_name"] = agent_name

            cursor = self.collection.find(query, {"_id": 0}).sort("timestamp", 1)
            messages = await cursor.to_list(length=1000)

            result = [
                {
                    "id": m.get("id"),
                    "role": m["role"],
                    "content": m["content"],
                    "session_id": m.get("session_id"),
                    "timestamp": (
                        m["timestamp"].timestamp()
                        if isinstance(m["timestamp"], datetime)
                        else m["timestamp"]
                    ),
                    "msg_metadata": m.get("msg_metadata"),
                }
                for m in messages
            ]

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

                summary_msg_doc = {
                    "id": summary_id,
                    "role": summary_msg["role"],
                    "content": summary_msg["content"],
                    "msg_metadata": summary_msg["msg_metadata"],
                    "session_id": session_id,
                    "timestamp": utc_now_str(),
                    "status": "active",
                    "inactive_reason": None,
                    "summary_id": None,
                }

                async def _background_persist_summary():
                    try:
                        await self._ensure_connected()

                        await self.collection.insert_one(summary_msg_doc)

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
                        logger.error(f"Background MongoDB persistence failed: {e}")

                asyncio.create_task(_background_persist_summary())

            return result

        except Exception as e:
            logger.error(f"Failed to get messages: {e}")
            return []

    async def clear_memory(
        self, session_id: str = None, agent_name: str = None
    ) -> None:
        try:
            await self._ensure_connected()
            query = {}
            if session_id:
                query["session_id"] = session_id
            if agent_name:
                query["msg_metadata.agent_name"] = agent_name
            await self.collection.delete_many(query)
        except Exception as e:
            logger.error(f"Failed to clear memory: {e}")

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

        try:
            await self._ensure_connected()

            if retention_policy == "delete":
                result = await self.collection.delete_many({"id": {"$in": message_ids}})
                logger.debug(f"Deleted {result.deleted_count} summarized messages")
            else:
                result = await self.collection.update_many(
                    {"id": {"$in": message_ids}},
                    {
                        "$set": {
                            "status": "inactive",
                            "inactive_reason": "summarized",
                            "summary_id": summary_id,
                        }
                    },
                )
                logger.debug(
                    f"Marked {result.modified_count} messages as summarized (inactive)"
                )

        except Exception as e:
            logger.error(f"Failed to mark messages as summarized: {e}")

    # --- run state ---------------------------------------------------------
    # One document per run in "<collection>_run_states"; updates match on the
    # version, so a stale writer changes nothing.

    async def save_run_state(self, record: dict, expected_version: int | None) -> int:
        from omnicoreagent.core.runs import RunStateConflict

        await self._ensure_connected()
        run_id = record["run_id"]
        version = (expected_version or 0) + 1
        document = {**record, "version": version}
        if expected_version is None:
            try:
                await self.run_states.insert_one({"_id": run_id, **document})
            except errors.DuplicateKeyError:
                raise RunStateConflict(f"Run {run_id} already exists") from None
            return version
        result = await self.run_states.replace_one(
            {"_id": run_id, "version": expected_version}, {"_id": run_id, **document}
        )
        if result.matched_count != 1:
            raise RunStateConflict(f"Run {run_id} changed since version {expected_version}")
        return version

    async def get_run_state(self, run_id: str) -> dict | None:
        await self._ensure_connected()
        document = await self.run_states.find_one({"_id": run_id})
        if document is None:
            return None
        document.pop("_id", None)
        return document

    async def list_run_states(
        self, session_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict]:
        await self._ensure_connected()
        query = {}
        if session_id is not None:
            query["session_id"] = session_id
        if status is not None:
            query["status"] = status
        records = []
        async for document in self.run_states.find(query).sort("created_at", 1).limit(limit):
            document.pop("_id", None)
            records.append(document)
        return records

    async def delete_finished_run_states(self, *, before: str, statuses: tuple[str, ...]) -> int:
        await self._ensure_connected()
        result = await self.run_states.delete_many(
            {"status": {"$in": list(statuses)}, "created_at": {"$lt": before}}
        )
        return int(result.deleted_count)

    # --- budgets -----------------------------------------------------------
    # One document per budget key: counters per meter (``$inc``) and one
    # sub-record per hold, set and unset in place. MongoDB makes a single-
    # document update atomic, and a filter on the update is checked in the same
    # step, so the limit check and the increment are one operation; holds stay
    # in the document, not a second collection, because two collections would
    # need a transaction, which a standalone server does not have. Nothing is
    # read, changed in Python and written back, so a crowd of runs on one key
    # neither passes the limit nor conflicts with itself (the support desk
    # ramp, 2026-10-07). The document is bounded: six meters and the holds of
    # runs in flight.

    async def delete_budget_state(self, key: str) -> None:
        await self._ensure_connected()
        await self.budget_states.delete_one({"_id": key})
        await self.budgets.delete_one({"_id": key})

    async def _move_legacy_budget(self, key: str) -> None:
        await self._ensure_connected()
        if key in self._legacy_budgets_moved:
            return
        legacy = await self.budget_states.find_one_and_delete({"_id": key})
        if legacy is not None:
            legacy.pop("_id", None)
            parts = legacy_budget_parts(legacy)
            increments = {
                f"meters.{meter}.{name}": amount
                for meter, values in parts["counters"].items()
                for name, amount in values.items()
                if amount
            }
            update: dict[str, Any] = {}
            if increments:
                update["$inc"] = increments
            if parts["holds"]:
                update["$set"] = {f"holds.{i}": h for i, h in parts["holds"].items()}
            if parts["history"]:
                update["$push"] = {
                    "grants": {"$each": parts["history"], "$slice": -GRANT_HISTORY_KEPT}
                }
            try:
                if update:
                    await self.budgets.update_one({"_id": key}, update, upsert=True)
            except BaseException:
                # Put it back rather than lose it; the next touch tries again.
                await self.budget_states.replace_one({"_id": key}, {"_id": key, **legacy}, upsert=True)
                raise
        if len(self._legacy_budgets_moved) > 10_000:
            self._legacy_budgets_moved.clear()
        self._legacy_budgets_moved.add(key)

    @staticmethod
    def _budget_counters(document: dict | None) -> dict[str, dict[str, float]]:
        return {
            meter: {
                "spent": float(values.get("spent", 0.0)),
                "reserved": float(values.get("reserved", 0.0)),
                "granted": float(values.get("granted", 0.0)),
            }
            for meter, values in ((document or {}).get("meters") or {}).items()
        }

    async def get_budget_state(self, key: str) -> dict | None:
        await self._move_legacy_budget(key)
        document = await self.budgets.find_one({"_id": key})
        return counters_view(key, self._budget_counters(document))

    async def get_budget_grant_history(self, key: str) -> list[dict]:
        await self._move_legacy_budget(key)
        document = await self.budgets.find_one({"_id": key}, {"grants": 1})
        return list((document or {}).get("grants") or [])

    async def list_budget_holds(self, key: str) -> list[dict]:
        await self._move_legacy_budget(key)
        document = await self.budgets.find_one({"_id": key}, {"holds": 1})
        return [{"id": i, **held} for i, held in ((document or {}).get("holds") or {}).items()]

    async def apply_budget_change(self, key: str, change: dict) -> dict:
        await self._move_legacy_budget(key)
        checks = budget_checks(change)
        for _ in range(_BUDGET_UPDATE_TRIES):
            document = await self.budgets.find_one({"_id": key})
            counters = self._budget_counters(document)
            held = (document or {}).get("holds") or {}

            # What this change removes, from what the document holds now.
            removing: dict[str, dict] = {}
            settle = change.get("settle")
            if settle and settle["id"] in held:
                removing[settle["id"]] = held[settle["id"]]
            wanted = set(change.get("release_runs") or [])
            released = 0
            if wanted:
                for hold_id, record in held.items():
                    if record.get("run_id") in wanted and hold_id not in removing:
                        removing[hold_id] = record
                        released += 1

            refused = refusal_for(checks, counters)
            if refused:
                return {"refused": refused, "totals": {}, "released": 0}

            increments: dict[str, float] = {}
            touched: list[str] = []

            def inc(meter: str, name: str, amount: float) -> None:
                if amount:
                    path = f"meters.{meter}.{name}"
                    increments[path] = increments.get(path, 0.0) + amount

            for record in removing.values():
                inc(record["meter"], "reserved", -float(record["amount"]))
            if settle and settle.get("spend") is not None and (
                settle["id"] in removing or settle.get("even_if_released")
            ):
                inc(settle["meter"], "spent", float(settle["spend"]))
                touched.append(settle["meter"])
            hold = change.get("hold")
            if hold and hold.get("amount"):
                inc(hold["meter"], "reserved", float(hold["amount"]))
            for meter, amount, _ in change.get("guard") or []:
                inc(meter, "spent", float(amount))
                touched.append(meter)
            for meter, amount in change.get("add") or []:
                inc(meter, "spent", float(amount))
                touched.append(meter)
            grant = change.get("grant")
            if grant:
                inc(grant["meter"], "granted", float(grant["amount"]))

            update: dict[str, Any] = {}
            if increments:
                update["$inc"] = increments
            if removing or (hold and hold.get("amount")):
                update["$unset"] = {f"holds.{i}": "" for i in removing}
            if hold and hold.get("amount"):
                update["$set"] = {
                    f"holds.{hold['id']}": {
                        "meter": hold["meter"],
                        "amount": float(hold["amount"]),
                        "run_id": hold.get("run_id"),
                        "held_at": hold.get("held_at"),
                    }
                }
            if grant:
                update["$push"] = {"grants": {"$each": [grant], "$slice": -GRANT_HISTORY_KEPT}}
            if not update.get("$unset"):
                update.pop("$unset", None)
            if not update:
                return {"refused": None, "totals": {}, "released": released}

            # The filter is checked in the same atomic step as the update: the
            # limits still fit, and each hold being removed is still there.
            conditions: list[dict] = [{"_id": key}]
            for meter, amount, limit in checks:
                if limit is None:
                    continue
                conditions.append(
                    {
                        "$expr": {
                            "$lte": [
                                {
                                    "$add": [
                                        {"$ifNull": [f"$meters.{meter}.spent", 0]},
                                        {"$ifNull": [f"$meters.{meter}.reserved", 0]},
                                        amount,
                                    ]
                                },
                                {"$add": [float(limit), {"$ifNull": [f"$meters.{meter}.granted", 0]}]},
                            ]
                        }
                    }
                )
            for hold_id in removing:
                conditions.append({f"holds.{hold_id}": {"$exists": True}})
            after = await self.budgets.find_one_and_update(
                {"$and": conditions}, update, return_document=ReturnDocument.AFTER
            )
            if after is not None:
                after_counters = self._budget_counters(after)
                return {
                    "refused": None,
                    "totals": {m: after_counters[m]["spent"] for m in touched},
                    "released": released,
                }
            if document is None:
                # The key has no document yet: make it, and go again.
                await self.budgets.update_one(
                    {"_id": key}, {"$setOnInsert": {"meters": {}, "holds": {}}}, upsert=True
                )
        raise RuntimeError(f"Could not record the budget change for {key}")

    async def save_budget_state(self, state: dict, expected_version: int | None) -> int:
        from omnicoreagent.core.runs import RunStateConflict

        await self._ensure_connected()
        key = state["key"]
        # A document written now has not been looked at yet.
        self._legacy_budgets_moved.discard(key)
        version = (expected_version or 0) + 1
        document = {**state, "version": version}
        if expected_version is None:
            try:
                await self.budget_states.insert_one({"_id": key, **document})
            except errors.DuplicateKeyError:
                raise RunStateConflict(f"Budget {key} already exists") from None
            return version
        result = await self.budget_states.replace_one(
            {"_id": key, "version": expected_version}, {"_id": key, **document}
        )
        if result.matched_count != 1:
            raise RunStateConflict(f"Budget {key} changed since version {expected_version}")
        return version
