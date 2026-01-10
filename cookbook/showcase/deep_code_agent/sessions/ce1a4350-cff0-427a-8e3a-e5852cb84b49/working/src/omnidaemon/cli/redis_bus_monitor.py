import asyncio
import json
import logging
import signal
import sys
import time
from typing import Dict, Any, Optional, List

from tabulate import tabulate
from decouple import config
from omnidaemon.event_bus.redis_stream_bus import RedisStreamEventBus

try:
    from colorama import init as colorama_init, Fore, Style

    colorama_init()
except Exception:

    class Fore:
        RED = "\033[31m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        BLUE = "\033[34m"
        RESET = "\033[0m"

    class Style:
        BRIGHT = "\033[1m"
        NORMAL = "\033[0m"


logger = logging.getLogger("omni.bus")


def clear_screen():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def color_for_dlq(dlq_count: int) -> str:
    return Fore.RED + Style.BRIGHT if dlq_count > 0 else Fore.GREEN


def color_for_pending(pending: int) -> str:
    if pending > 50:
        return Fore.RED + Style.BRIGHT
    if pending > 0:
        return Fore.YELLOW
    return Fore.GREEN


# -------------------------
# STATELESS BUS FUNCTIONS
# -------------------------


async def bus_list_streams(redis_url: str):
    bus = RedisStreamEventBus(redis_url=redis_url)
    await bus.connect()
    try:
        keys = await bus._redis.keys("omni-stream:*")
        keys = [k.decode() if isinstance(k, bytes) else k for k in keys]
        streams = [k for k in keys if not k.startswith("dlq:")]
        rows = []
        for s in streams:
            length = await bus._redis.xlen(s)
            rows.append([s, length])
        print(tabulate(rows, headers=["Stream", "Count"], tablefmt="github"))
    finally:
        await bus._redis.close()


async def bus_inspect_stream(stream: str, redis_url: str, limit: int = 5):
    bus = RedisStreamEventBus(redis_url=redis_url)
    await bus.connect()
    try:
        stream_key = (
            f"omni-stream:{stream}" if not stream.startswith("omni-stream:") else stream
        )
        entries = await bus._redis.xrevrange(stream_key, count=limit)
        if not entries:
            print(f"No entries in {stream_key}")
            return
        for msg_id, fields in entries:
            msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
            data_field = fields.get(b"data") or fields.get("data", b"")
            data_str = (
                data_field.decode() if isinstance(data_field, bytes) else data_field
            )
            print(f"ID: {msg_id_str}")
            try:
                print(json.dumps(json.loads(data_str), indent=2))
            except Exception:
                print(data_str)
            print("-" * 40)
    finally:
        await bus._redis.close()


async def bus_list_groups(stream: str, redis_url: str):
    bus = RedisStreamEventBus(redis_url=redis_url)
    await bus.connect()
    try:
        stream_key = (
            f"omni-stream:{stream}" if not stream.startswith("omni-stream:") else stream
        )
        try:
            groups = await bus._redis.xinfo_groups(stream_key)
        except Exception:
            print(f"No groups for {stream_key}")
            return
        rows = []
        for g in groups:
            name = g.get("name", b"")
            name = name.decode() if isinstance(name, bytes) else name
            consumers = g.get("consumers", 0)
            pending = g.get("pending", 0)
            last_id = g.get("last-delivered-id", b"")
            last_id = last_id.decode() if isinstance(last_id, bytes) else last_id
            rows.append([name, consumers, pending, last_id])
        print(
            tabulate(
                rows,
                headers=["Group", "Consumers", "Pending", "LastDelivered"],
                tablefmt="github",
            )
        )
    finally:
        await bus._redis.close()


async def bus_inspect_dlq(topic: str, redis_url: str, limit: int = 5):
    """Inspect dead-letter queue for a topic."""
    bus = RedisStreamEventBus(redis_url=redis_url)
    await bus.connect()
    try:
        all_dlq = await bus._redis.keys("omni-dlq:*")
        per_topic_dlq = await bus._redis.keys(f"omni-dlq:group:{topic}:*")
        dlq_keys = set(all_dlq).intersection(set(per_topic_dlq))
        if not dlq_keys:
            print(f"No DLQ for topic {topic}")
            return
        dlq_key = dlq_keys.pop()
        entries = await bus._redis.xrevrange(dlq_key.encode(), count=limit)
        if not entries:
            print(f"No entries in DLQ for topic {topic}")
            return
        for msg_id, data in entries:
            print(f"DLQ ID: {msg_id}")
            try:
                print(json.dumps(data, indent=2))
            except Exception:
                print(data)
            print("-" * 40)
    finally:
        await bus._redis.close()


async def bus_get_stats(redis_url: str, as_json: bool = False):
    """Get one-shot stats across all topics."""
    bus = RedisStreamEventBus(redis_url=redis_url)
    await bus.connect()
    try:
        # Get all streams
        stream_keys = await bus._redis.keys("omni-stream:*")
        stream_keys = [k.decode() if isinstance(k, bytes) else k for k in stream_keys]
        stream_keys = [k for k in stream_keys if not k.startswith("dlq:")]

        snapshot = {"timestamp": time.time(), "topics": {}}
        for stream_key in stream_keys:
            topic = stream_key.replace("omni-stream:", "", 1)
            stream_name = stream_key

            # Stream length
            length = await bus._redis.xlen(stream_name)

            # Get groups
            groups = []
            dlq_total = 0
            try:
                group_infos = await bus._redis.xinfo_groups(stream_name)
                for g in group_infos:
                    name = (
                        g.get("name", "").decode()
                        if isinstance(g.get("name"), bytes)
                        else g.get("name", "")
                    )
                    consumers = g.get("consumers", 0)
                    pending = g.get("pending", 0)
                    last_id = (
                        g.get("last-delivered-id", b"").decode()
                        if isinstance(g.get("last-delivered-id"), bytes)
                        else g.get("last-delivered-id", "")
                    )

                    # Get DLQ length for this group
                    dlq_key = f"dlq:{name}"
                    try:
                        dlq_len = await bus._redis.xlen(dlq_key)
                        dlq_total += dlq_len
                    except Exception:
                        dlq_len = 0

                    groups.append(
                        {
                            "name": name,
                            "consumers": consumers,
                            "pending": pending,
                            "last_delivered_id": last_id,
                            "dlq": dlq_len,
                        }
                    )
            except Exception as e:
                logger.warning(f"Failed to get groups for {stream_name}: {e}")
                groups = []

            snapshot["topics"][topic] = {
                "length": length,
                "dlq_total": dlq_total,
                "groups": groups,
            }

        # Redis memory
        redis_info = {"used_memory_human": "-"}
        try:
            info = await bus._redis.info()
            redis_info["used_memory_human"] = info.get("used_memory_human", "-")
        except Exception:
            pass

        result = {"snapshot": snapshot, "redis_info": redis_info}

        if as_json:
            print(json.dumps(result, indent=2, default=str))
        else:
            rows = []
            for topic, data in snapshot["topics"].items():
                for group in data["groups"]:
                    rows.append(
                        [
                            topic,
                            data["length"],
                            group["pending"],
                            group["dlq"],
                            group["name"],
                            group["consumers"],
                        ]
                    )
            print("Snapshot (per consumer group):")
            print(
                tabulate(
                    rows,
                    headers=[
                        "Topic",
                        "StreamLen",
                        "Pending",
                        "DLQ",
                        "Group",
                        "Consumers",
                    ],
                    tablefmt="github",
                )
            )
    finally:
        await bus._redis.close()


async def bus_watch_live(redis_url: str, interval: int = 2, as_json: bool = False):
    """Live dashboard — one row per consumer group."""
    bus = RedisStreamEventBus(redis_url=redis_url)
    await bus.connect()

    print("Starting OmniDaemon Live Monitor. Ctrl-C to exit.\n")
    gen = bus.monitor_generator()
    stop_future = asyncio.get_event_loop().create_future()

    def _signal_handler():
        if not stop_future.done():
            stop_future.set_result(True)

    try:
        asyncio.get_event_loop().add_signal_handler(signal.SIGINT, _signal_handler)
        asyncio.get_event_loop().add_signal_handler(signal.SIGTERM, _signal_handler)
    except NotImplementedError:
        pass

    # State: key = group_name (since group names are globally unique)
    group_state: Dict[str, Dict[str, Any]] = {}
    group_counters: Dict[str, Dict[str, int]] = {}
    last_processed: Dict[str, int] = {}
    last_time = time.time()

    async def aggregator():
        async for ev in gen:
            if not ev:
                continue
            evt = ev.get("event")
            if evt == "snapshot":
                snap = ev.get("data", {})
                # New format: snap = { "groups": [ { "group": "...", "pending": N, "dlq": M, "counters": {...} }, ... ] }
                for group_data in snap.get("groups", []):
                    group_name = group_data.get("group")
                    if not group_name:
                        continue
                    group_state[group_name] = {
                        "topic": group_data.get("topic", "unknown"),
                        "pending": group_data.get("pending", 0),
                        "dlq": group_data.get("dlq", 0),
                        "counters": group_data.get("counters", {}),
                    }
            else:
                # Real-time events
                group = ev.get("group")
                if not group:
                    continue
                if group not in group_counters:
                    group_counters[group] = {
                        "processed": 0,
                        "dlq_push": 0,
                        "reclaimed": 0,
                        "reclaim_attempt": 0,
                    }
                if evt == "processed":
                    group_counters[group]["processed"] += 1
                elif evt == "dlq_push":
                    group_counters[group]["dlq_push"] += 1
                elif evt == "reclaimed":
                    group_counters[group]["reclaimed"] += 1
                elif evt == "reclaim_attempt":
                    group_counters[group]["reclaim_attempt"] += 1

    agg_task = asyncio.create_task(aggregator())

    try:
        while not stop_future.done():
            # Discover all groups
            stream_keys = await bus._redis.keys("omni-stream:*")
            stream_keys = [
                k.decode() if isinstance(k, bytes) else k for k in stream_keys
            ]
            stream_keys = [k for k in stream_keys if not k.startswith("dlq:")]

            all_groups = {}
            for stream_key in stream_keys:
                topic = stream_key.replace("omni-stream:", "", 1)
                try:
                    groups = await bus._redis.xinfo_groups(stream_key)
                    for g in groups:
                        name = g.get("name", b"")
                        name = name.decode() if isinstance(name, bytes) else name
                        pending = g.get("pending", 0)
                        dlq_len = 0
                        try:
                            dlq_len = await bus._redis.xlen(f"omni-dlq:{name}")
                        except Exception:
                            pass
                        all_groups[name] = {
                            "topic": topic,
                            "pending": pending,
                            "dlq": dlq_len,
                        }
                except Exception:
                    continue

            # Build rows
            rows = []
            now = time.time()
            dt = now - last_time or interval

            for group_name, base in all_groups.items():
                state = group_state.get(group_name, {})
                cnt = group_counters.get(
                    group_name,
                    {
                        "processed": 0,
                        "dlq_push": 0,
                        "reclaimed": 0,
                        "reclaim_attempt": 0,
                    },
                )

                last_proc = last_processed.get(group_name, cnt["processed"])
                rate = (cnt["processed"] - last_proc) / max(dt, 0.1)
                last_processed[group_name] = cnt["processed"]

                if not as_json:
                    pending_str = f"{color_for_pending(base['pending'])}{base['pending']}{Fore.RESET}"
                    dlq_str = f"{color_for_dlq(base['dlq'])}{base['dlq']}{Fore.RESET}"
                else:
                    pending_str = base["pending"]
                    dlq_str = base["dlq"]

                rows.append(
                    [
                        base["topic"],
                        pending_str,
                        dlq_str,
                        group_name,
                        cnt["processed"],
                        cnt["dlq_push"],
                        cnt["reclaimed"],
                        cnt["reclaim_attempt"],
                        f"{rate:.2f}",
                    ]
                )

            if as_json:
                output = [
                    {
                        "topic": r[0],
                        "pending": r[1],
                        "dlq": r[2],
                        "group": r[3],
                        "processed": r[4],
                        "dlq_push": r[5],
                        "reclaimed": r[6],
                        "reclaim_attempt": r[7],
                        "rate": float(r[8]),
                    }
                    for r in rows
                ]
                print(json.dumps(output, indent=2))
            else:
                clear_screen()
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
                header = f"OmniDaemon Live Monitor  |  {ts}"
                print(header)
                print("=" * len(header))
                print(
                    tabulate(
                        rows,
                        headers=[
                            "Topic",
                            "Pending",
                            "DLQ",
                            "Group",
                            "Processed",
                            "DLQ",
                            "Reclaimed",
                            "ReclaimAttempt",
                            "Proc/s",
                        ],
                        tablefmt="github",
                    )
                )
                try:
                    info = await bus._redis.info()
                    mem = info.get("used_memory_human", "-")
                except Exception:
                    mem = "-"
                print(f"\nRedis Memory: {mem}  |  Groups: {len(rows)}")
                print("Ctrl-C to quit.")

            last_time = now
            await asyncio.sleep(interval)

    finally:
        agg_task.cancel()
        await bus._redis.close()
        print("\nExiting monitor...")
