"""Load test for the support desk: a ramp, or a soak, of simulated customers.

    python apps/support_desk/load/loadtest.py --mode ramp
    python apps/support_desk/load/loadtest.py --mode soak
    python apps/support_desk/load/loadtest.py --mode smoke        # a minute, a few users

The desk must run with ``DESK_PROFILE=load`` (budgets that never stop the
load; the debug routes for the ledger and the event-loop lag). See README.md.

It writes ``result.json`` and ``report.md`` to ``--out``. The finish-line
numbers of ``engineering/architecture/production-readiness-plan.md`` sit next
to their targets as PASS or MISS; a run below the plan's scale says so, so a
small local check is never mistaken for the real one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from loadlib import (  # noqa: E402
    Desk, Load, Sampler, check_ledger, error_table, fake_get, find_stuck, find_waiting,
    per_kind, prime_fake, read_ledger, runtime_overhead, summarize_resources, summarize,
)

MODES = {
    # The plan's concurrency finish line: 100 users for 10 minutes, reached by 10 then 50.
    "ramp": [(10, 120), (50, 120), (100, 600)],
    # The plan's soak: 50 users for 30 minutes.
    "soak": [(50, 1800)],
    "smoke": [(3, 30), (6, 45)],
}
PLAN = {"concurrent_users": 100, "concurrent_seconds": 600, "soak_users": 50, "soak_seconds": 1800}
# Finish-line targets.
TARGET_OVERHEAD_P95_MS = 100
TARGET_LAG_MS = 500
TARGET_MEM_GROWTH = 0.15


def parse_stages(text: str) -> list[tuple[int, float]]:
    return [(int(a), float(b)) for a, b in (part.split(":") for part in text.split(","))]


def arguments() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=sorted(MODES), default="ramp")
    p.add_argument("--stages", help="override the mode: users:seconds,users:seconds (e.g. 10:60,50:60)")
    p.add_argument("--base-url", default=os.environ.get("DESK_URL", "http://127.0.0.1:8800"))
    p.add_argument("--fake-url", default=os.environ.get("DESK_FAKE_URL_PUBLIC", "http://127.0.0.1:9000"))
    p.add_argument("--token", default=os.environ.get("OMNICOREAGENT_SERVE_AUTH_TOKEN", "change-me"))
    p.add_argument("--project", default=os.environ.get("DESK_PROJECT", "support-desk"), help="the Compose project name")
    p.add_argument("--out", help="output directory (default: load/results/<mode>-<time>)")
    p.add_argument("--model-latency", default="0.8,3.0", help="the fake provider's answer delay: min,max seconds")
    p.add_argument("--kb-share", type=float, default=0.3, help="share of visits that ask a help question, not for a refund")
    p.add_argument("--stream-share", type=float, default=0.2, help="share of help questions answered over SSE")
    p.add_argument("--deny-share", type=float, default=0.1, help="share of refunds the staff deny")
    p.add_argument("--think", default="1,3", help="a customer's pause between visits: min,max seconds")
    p.add_argument("--sample-interval", type=float, default=5.0)
    p.add_argument("--overhead-sample", type=int, default=200, help="runs whose trajectory is read for the overhead")
    p.add_argument("--seed", type=int, default=None)
    return p.parse_args()


def _pair(text: str) -> tuple[float, float]:
    low, high = (float(x) for x in text.split(","))
    return low, high


async def preflight(desk: Desk) -> None:
    ready, error = await desk.quiet("GET", "/ready")
    if error or not ready.get("ready"):
        sys.exit(f"The desk is not ready at {desk.base_url}: {error or ready}")
    lag, error = await desk.quiet("GET", "/_debug/lag")
    if error:
        sys.exit(f"{desk.base_url}/_debug/lag answered {error}: start the desk with DESK_PROFILE=load (see README.md).")


async def main() -> int:
    args = arguments()
    stages = parse_stages(args.stages) if args.stages else MODES[args.mode]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = Path(args.out or Path(__file__).parent / "results" / f"{args.mode}-{stamp}")
    out.mkdir(parents=True, exist_ok=True)

    desk = Desk(args.base_url, args.token)
    await preflight(desk)
    latency = _pair(args.model_latency)
    await prime_fake(args.fake_url, reset=True, latency_min=latency[0], latency_max=latency[1],
                     rate_429=0, rate_500=0, rate_timeout=0, rate_slow_stream=0)
    baseline = await read_ledger(desk) or []
    tag = f"load{stamp[-6:]}"
    load = Load(
        desk, tag=tag, kb_share=args.kb_share, stream_share=args.stream_share, deny_share=args.deny_share,
        think=_pair(args.think), used_amounts={(r["order_id"], round(r["amount"] * 100)) for r in baseline},
        seed=args.seed,
    )
    sampler = Sampler(desk, args.project, interval=args.sample_interval)
    max_users = max(users for users, _ in stages)
    # One staff member per five customers is plenty: an approval takes about a second.
    load.start_staff(max(2, max_users // 5))

    print(f"[load] {args.mode}: stages {stages}, tag {tag}, out {out}", flush=True)
    sampler.start()
    began = time.time()
    boundaries = []
    for index, (users, seconds) in enumerate(stages):
        desk.stage = index
        load.add_users(users - len(load.users))
        t_start = time.time()
        print(f"[load] stage {index}: {users} users for {seconds:.0f}s", flush=True)
        await asyncio.sleep(seconds)
        boundaries.append({"stage": index, "users": users, "seconds": seconds, "t_start": t_start, "t_end": time.time()})
        done = [r for r in desk.records if r.stage == index]
        print(f"[load]   {len(done)} requests, {sum(1 for r in done if r.error)} errors", flush=True)
    load_ended = time.time()
    print("[load] stopping: letting visits under way finish", flush=True)
    await load.stop()
    drained = time.time()
    await sampler.stop()
    sampler.samples.append(await sampler.sample())

    # Correctness, once the load has stopped.
    ledger = await read_ledger(desk)
    ledger_check = (
        check_ledger(load.attempts, ledger, {r["id"] for r in baseline}) if ledger is not None
        else {"ok": False, "error": "could not read /_debug/ledger"}
    )
    stuck = await find_stuck(desk, tag)
    waiting = await find_waiting(desk, tag)

    # Runtime overhead: a spread sample of runs per stage, read after the load.
    sample_ids = pick_runs(load, boundaries, args.overhead_sample)
    overhead = {"overall": await runtime_overhead(desk, [r for ids in sample_ids.values() for r in ids])}
    for stage, ids in sample_ids.items():
        overhead[f"stage_{stage}"] = await runtime_overhead(desk, ids)
    fake = await fake_get(args.fake_url, "/_stats")
    await desk.close()

    result = build_result(args, stages, boundaries, desk.records, sampler.samples, load, overhead,
                          ledger_check, stuck, waiting, fake, began, load_ended, drained)
    (out / "result.json").write_text(json.dumps(result, indent=2, default=str))
    (out / "report.md").write_text(render_report(result))
    with open(out / "requests.ndjson", "w") as raw:
        for r in desk.records:
            raw.write(json.dumps(r.__dict__) + "\n")
    print((out / "report.md").read_text())
    print(f"[load] wrote {out}/result.json and report.md")
    return 0 if result["correctness"]["ok"] else 1


def pick_runs(load: Load, boundaries: list[dict], total: int) -> dict[int, list[str]]:
    """An even spread of the runs of each stage, ``total`` in all."""
    per_stage: dict[int, list[str]] = {b["stage"]: [] for b in boundaries}
    for run_id, info in load.run_ids.items():
        for b in boundaries:
            if b["t_start"] <= info["t"] <= b["t_end"] + 1:
                per_stage[b["stage"]].append(run_id)
                break
    each = max(1, total // max(1, len(per_stage)))
    picked = {}
    for stage, ids in per_stage.items():
        step = max(1, len(ids) // each)
        picked[stage] = ids[::step][:each]
    return picked


def build_result(args, stages, boundaries, records, samples, load, overhead, ledger_check, stuck, waiting,
                 fake, began, load_ended, drained) -> dict:
    stage_rows = []
    for b in boundaries:
        inside = [r for r in records if b["t_start"] <= r.t0 < b["t_end"] and not r.kind.endswith("_first_byte")]
        resources = summarize_resources(samples_between(samples, b["t_start"], b["t_end"]))
        stage_rows.append({
            **b, "requests": len(inside), "errors": sum(1 for r in inside if r.error),
            "requests_per_second": len(inside) / b["seconds"],
            "latency_ms": summarize([r.seconds * 1000 for r in inside]),
            "by_kind": per_kind(inside), "resources": resources,
        })
    real = [r for r in records if not r.kind.endswith("_first_byte")]
    top = max(boundaries, key=lambda b: b["users"])
    result = {
        "mode": args.mode, "stages": stage_rows, "started": began, "load_ended": load_ended, "drained": drained,
        "config": {k: v for k, v in vars(args).items() if k != "token"},
        "totals": {
            "requests": len(real), "errors": sum(1 for r in real if r.error),
            "seconds": load_ended - began,
            "requests_per_second": len(real) / (load_ended - began),
            "visits_started": load.visits, "visits_completed": load.completed_visits,
            "visits_per_second": load.completed_visits / (load_ended - began),
            "latency_ms": summarize([r.seconds * 1000 for r in real]),
        },
        "by_kind": per_kind(real),
        "errors_by_kind": error_table(real),
        "first_byte_ms": summarize([r.seconds * 1000 for r in records if r.kind.endswith("_first_byte")]),
        "resources": summarize_resources(samples),
        "samples": samples,
        "overhead": overhead,
        "correctness": {"ok": None, "ledger": ledger_check, "stuck_runs": stuck, "refunds_waiting_for_a_person": waiting},
        "fake_provider": fake,
        "harness_errors": [r.error for r in records if r.kind == "harness_error"][:20],
    }
    result["correctness"]["ok"] = bool(ledger_check.get("ok") and not stuck and not waiting and not result["harness_errors"])
    result["finish_lines"] = finish_lines(result, top)
    return result


def samples_between(samples, t0, t1):
    return [s for s in samples if t0 <= s["t"] <= t1]


def verdict(ok: bool, at_scale: bool) -> str:
    return ("PASS" if ok else "MISS") + ("" if at_scale else " (below plan scale)")


def finish_lines(result: dict, top: dict) -> list[dict]:
    """The plan's finish lines 1 and 2, measured, each next to its target."""
    lines = []
    stages = result["stages"]
    # 1. Concurrency: 100 sessions for 10 minutes.
    stage = next((s for s in stages if s["users"] == top["users"]), stages[-1])
    at_scale = stage["users"] >= PLAN["concurrent_users"] and stage["seconds"] >= PLAN["concurrent_seconds"]
    failed = stage["errors"]
    overhead_p95 = (result["overhead"].get(f"stage_{stage['stage']}") or result["overhead"]["overall"])["step_overhead_ms"]["p95"]
    stall = stage["resources"].get("lag_max_ms")
    lines += [
        {"line": "1. Concurrency", "measure": f"failed requests at {stage['users']} users x {stage['seconds']:.0f}s",
         "value": failed, "target": "0 (none injected)", "verdict": verdict(failed == 0, at_scale)},
        {"line": "1. Concurrency", "measure": "runtime overhead per step, p95 (ms, excluding model time)",
         "value": None if overhead_p95 is None else round(overhead_p95, 1), "target": f"< {TARGET_OVERHEAD_P95_MS}",
         "verdict": verdict(overhead_p95 is not None and overhead_p95 < TARGET_OVERHEAD_P95_MS, at_scale)},
        {"line": "1. Concurrency", "measure": "worst event-loop stall (ms)",
         "value": None if stall is None else round(stall, 1), "target": f"< {TARGET_LAG_MS}",
         "verdict": verdict(stall is not None and stall < TARGET_LAG_MS, at_scale)},
    ]
    # 2. Soak: memory after warm-up, connections bounded.
    soak_scale = result["mode"] == "soak" and top["users"] >= PLAN["soak_users"] and top["seconds"] >= PLAN["soak_seconds"]
    memory = memory_growth(result["samples"], result["started"], result["load_ended"])
    pg = connections_bounded(result["samples"], result["started"], result["load_ended"])
    lines += [
        {"line": "2. Soak", "measure": "memory growth after warm-up (last minutes vs end of warm-up)",
         "value": None if memory is None else f"{memory * 100:.1f}%", "target": f"< {TARGET_MEM_GROWTH * 100:.0f}%",
         "verdict": verdict(memory is not None and memory < TARGET_MEM_GROWTH, soak_scale)},
        {"line": "2. Soak", "measure": "Postgres connections stay bounded after warm-up",
         "value": pg["detail"], "target": "max within 25% (or 5) of the median", "verdict": verdict(pg["ok"], soak_scale)},
    ]
    lines.append({"line": "Correctness", "measure": "every approved refund in the ledger exactly once; no run stuck",
                  "value": "ok" if result["correctness"]["ok"] else "BROKEN", "target": "ok",
                  "verdict": verdict(bool(result["correctness"]["ok"]), True)})
    return lines


def _window(samples, started, ended, key):
    # The first fifth of the run (at most five minutes) is warm-up.
    warm = min(300, (ended - started) * 0.2)
    return [s[key] for s in samples if started + warm <= s["t"] <= ended and s.get(key) is not None], warm


def memory_growth(samples, started, ended) -> float | None:
    values, _ = _window(samples, started, ended, "mem_mib")
    if len(values) < 6:
        return None
    # Compare the average of the last minute with the average of the first
    # minute after warm-up: single samples are noisy.
    span = max(3, min(12, len(values) // 4))
    first, last = sum(values[:span]) / span, sum(values[-span:]) / span
    return (last - first) / first


def connections_bounded(samples, started, ended) -> dict:
    values, _ = _window(samples, started, ended, "pg_connections")
    if len(values) < 3:
        return {"ok": False, "detail": "too few samples"}
    values_sorted = sorted(values)
    median, peak = values_sorted[len(values) // 2], max(values)
    return {"ok": peak <= max(median * 1.25, median + 5), "detail": f"median {median}, max {peak}, last {values[-1]}"}


def ms(value) -> str:
    return "-" if value is None else f"{value:,.0f}"


def render_report(r: dict) -> str:
    t = r["totals"]
    lines = [
        f"# Support desk load report: {r['mode']}", "",
        f"Stages: {', '.join(f'{s['users']} users x {s['seconds']:.0f}s' for s in r['stages'])}. "
        f"{t['requests']:,} requests in {t['seconds']:.0f}s ({t['requests_per_second']:.1f}/s), "
        f"{t['errors']} errors, {t['visits_completed']:,} visits completed ({t['visits_per_second']:.2f}/s). "
        f"The model is the fake provider, {r['config']['model_latency']} s per answer.", "",
        "## Finish lines", "", "| Line | Measure | Value | Target | Verdict |", "|---|---|---|---|---|",
    ]
    for f in r["finish_lines"]:
        lines.append(f"| {f['line']} | {f['measure']} | {f['value']} | {f['target']} | **{f['verdict']}** |")
    lines += ["", "## Per stage", "",
              "| Users | Seconds | Requests | Req/s | Errors | p50 ms | p95 ms | p99 ms | Mem MiB (max) | CPU % (median/max) | PG conns (max) | Lag ms (max) |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in r["stages"]:
        res, lat = s["resources"], s["latency_ms"]
        mem = res.get("mem_mib", {}).get("max")
        cpu = res.get("cpu_pct")
        lines.append(
            f"| {s['users']} | {s['seconds']:.0f} | {s['requests']:,} | {s['requests_per_second']:.1f} | {s['errors']} | "
            f"{ms(lat['p50'])} | {ms(lat['p95'])} | {ms(lat['p99'])} | {ms(mem)} | "
            f"{ms(cpu['median']) + '/' + ms(cpu['max']) if cpu else '-'} | "
            f"{res.get('pg_connections', {}).get('max', '-')} | {res.get('lag_max_ms') if res.get('lag_max_ms') is not None else '-'} |"
        )
    lines += ["", "## Client latency by request (whole run)", "",
              "| Request | n | p50 ms | p95 ms | p99 ms | max ms | errors |", "|---|---|---|---|---|---|---|"]
    for kind, v in r["by_kind"].items():
        lines.append(f"| {kind} | {v['n']:,} | {ms(v['p50'])} | {ms(v['p95'])} | {ms(v['p99'])} | {ms(v['max'])} | {v['errors']} |")
    if r["first_byte_ms"]["n"]:
        fb = r["first_byte_ms"]
        lines.append(f"\nSSE time to first byte: n={fb['n']}, p50 {ms(fb['p50'])} ms, p95 {ms(fb['p95'])} ms.")
    lines += ["", "## Errors by kind", ""]
    lines.append("None." if not r["errors_by_kind"] else "\n".join(
        f"- `{error}`: {kinds}" for error, kinds in r["errors_by_kind"].items()))
    o = r["overhead"]["overall"]
    lines += ["", "## Runtime overhead (time not spent waiting on the model)", "",
              f"Read from the trajectories of {o['runs_read']} runs sampled across the stages. A step's overhead is its "
              "`duration_ms` minus the `latency_ms` of the model calls in it; tool time is included (see `tool ms`).", "",
              "| Measure | n | p50 | p95 | p99 | max |", "|---|---|---|---|---|---|"]
    for label, key in (("per step, all steps", "step_overhead_ms"), ("per step, steps with a model call", "step_overhead_ms_model_steps"),
                       ("per step, steps with no model call (a resumed tool)", "step_overhead_ms_tool_only_steps"),
                       ("per run segment, whole segment minus model", "run_non_model_ms"),
                       ("tool call time (inside the steps)", "tool_ms"), ("model latency per call (what is subtracted)", "model_latency_ms")):
        v = o[key]
        lines.append(f"| {label} (ms) | {v['n']} | {ms(v['p50'])} | {ms(v['p95'])} | {ms(v['p99'])} | {ms(v['max'])} |")
    lines += ["", "Per stage, steps with a model call, p50 / p95 ms: " + "; ".join(
        f"{k.replace('stage_', 'stage ')}: {ms(v['step_overhead_ms_model_steps']['p50'])} / {ms(v['step_overhead_ms_model_steps']['p95'])}"
        for k, v in r["overhead"].items() if k.startswith("stage_"))]
    c = r["correctness"]
    lines += ["", "## Correctness", "",
              f"- Refunds asked for: {c['ledger'].get('attempts')}; approved and completed: {c['ledger'].get('approved_and_completed')}; "
              f"new ledger rows: {c['ledger'].get('ledger_rows_new')}. Outcomes: {c['ledger'].get('outcomes')}",
              f"- Ledger problems: {c['ledger'].get('problems') or 'none'}",
              f"- Runs left stuck (running, interrupted, awaiting budget): {len(c['stuck_runs'])}",
              f"- Refunds left waiting for a person: {len(c['refunds_waiting_for_a_person'])}"]
    fp = r.get("fake_provider") or {}
    lines += ["", "## The fake provider saw", "",
              f"{fp.get('requests')} model requests, at most {fp.get('max_in_flight')} at once; statuses {fp.get('by_status')}."]
    res = r["resources"]
    lines += ["", "## Resources, whole run", ""]
    for key, label in (("mem_mib", "memory MiB"), ("cpu_pct", "CPU %"), ("pg_connections", "Postgres connections"), ("lag_window_max_ms", "event-loop lag ms (per 5 s)")):
        if key in res:
            v = res[key]
            lines.append(f"- {label}: min {v['min']:.0f}, median {v['median']:.0f}, max {v['max']:.0f}, last {v['last']:.0f}")
    lines.append(f"- Largest event-loop stall seen: {res.get('lag_max_ms')} ms; samples {res['samples']}.")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
