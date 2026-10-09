"""Observability check: can every fault of the chaos test be explained from the record?

    # the desk with Jaeger, then:
    python apps/support_desk/observe/check.py --run              # inject each fault once, then check
    python apps/support_desk/observe/check.py --chaos-result apps/support_desk/chaos/results/<x>/result.json

For each fault it finds a run the fault touched, and asks:

1. does the run's own trace say what happened, in an event or error text that
   names the cause (a provider 429, a timeout, a resume after a kill)?
2. does the same story show in Jaeger, for the trace the desk exported?
3. which signals does ``/prometheus`` have for it (runs, model errors,
   budget pauses, approvals waiting)?

and prints a checklist, PASS or GAP per fault. A fault that touched no run
is N/A, not PASS: there was nothing to explain, and the checklist says whether
anything anywhere records that it happened.

The desk must run with ``DESK_PROFILE=load`` and the Jaeger override
(``observe/compose.override.yml``). Nothing here spends money.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "load"))
sys.path.insert(0, str(HERE.parent / "chaos"))

import chaos  # noqa: E402
from loadlib import Desk  # noqa: E402

# What names the cause of each fault, as a pattern over the text of the run's
# events and errors. The same pattern is looked for in Jaeger's spans.
CAUSES = {
    "provider_429": r"rate.?limit|\b429\b|too many requests",
    "provider_500": r"\b500\b|server_error|internal ?server|error while processing",
    "provider_hang": r"timed? ?out|timeout",
    "provider_slow_stream": r"model_response|model_call",       # no error: the proof is latency, below
    "slow_tool": r"tool_call|tool_result",                        # no error: the proof is latency, below
    "tool_timeout": r"timed? ?out|timeout",
    "desk_kill": r"run_resumed",
    "desk_kill_in_refund": r"run_resumed",
    "postgres_restart": r"postgres|psycopg|sqlalchemy|operationalerror|server closed|connection (refused|reset|closed)|run_resumed",
    "postgres_kill": r"postgres|psycopg|sqlalchemy|operationalerror|server closed|connection (refused|reset|closed)|run_resumed",
    "redis_restart": r"redis",
}
# The metric each fault would show up in, were there one: a signal name below.
SIGNALS = {
    "runs": ("runs started, finished, failed or resumed", r"run"),
    "model_errors": ("model calls that failed or were retried", r"(model|llm).*(error|fail|retr)"),
    "tool_errors": ("tool calls that failed or timed out", r"tool.*(error|fail|timeout)"),
    "budget_pauses": ("runs paused by a budget", r"budget"),
    "approvals_waiting": ("approvals waiting for a person", r"approval"),
}
FAULT_SIGNAL = {
    "provider_429": "model_errors", "provider_500": "model_errors", "provider_hang": "model_errors",
    "provider_slow_stream": "model_errors", "slow_tool": "tool_errors", "tool_timeout": "tool_errors",
    "desk_kill": "runs", "desk_kill_in_refund": "runs", "postgres_restart": "runs", "postgres_kill": "runs",
    "redis_restart": "runs",
}
MIN_SLOW_MODEL_MS = 5000
MIN_SLOW_TOOL_S = 7.0


def jaeger_trace_id(trace_id: str) -> str:
    """The id Jaeger knows a trace by: the exporter hashes the runtime's id to 16 bytes."""
    return hashlib.sha256(trace_id.encode("utf-8")).digest()[:16].hex()


def _seconds(call: dict) -> float | None:
    if call.get("started_at") and call.get("ended_at"):
        return (datetime.fromisoformat(call["ended_at"]) - datetime.fromisoformat(call["started_at"])).total_seconds()
    return None


def story_texts(bundle: dict) -> list[tuple[str, str]]:
    """The run's events and errors as (where, text) pairs: what an on-call engineer would read."""
    out = []
    for event in sorted(bundle["events"], key=lambda e: e.get("sequence_number", 0)):
        text = event["event_type"]
        problem = event.get("error")
        if problem:
            text += f": {problem.get('type')}: {problem.get('message')}"
        meta = event.get("metadata") or {}
        if event["event_type"] == "run_resumed":
            text += f" (previous traces {meta.get('previous_trace_ids')}, approvals {len(meta.get('approvals') or [])})"
        out.append((event["event_type"], text))
    for segment in (bundle["trajectory"] or {}).get("segments", []):
        for step in (segment.get("trajectory") or {}).get("steps", []):
            for call in step.get("model_calls", []):
                for retry in (call.get("facts") or {}).get("retries") or []:
                    out.append(("model_retry", f"model_retry: {retry}"))
                if call.get("error"):
                    out.append(("model_call_error", f"model_call_error: {call['error']}"))
    if bundle["run"].get("error"):
        out.append(("run_record", f"run record error: {bundle['run']['error']}"))
    return out


def explain(fault: str, bundle: dict) -> tuple[bool, str]:
    """Does this run's record explain the fault? Returns (yes, the evidence)."""
    texts = story_texts(bundle)
    pattern = re.compile(CAUSES[fault], re.IGNORECASE)
    story = bundle["trajectory"] or {}
    if fault == "provider_slow_stream":
        slow = [
            (call.get("facts") or {}).get("latency_ms")
            for seg in story.get("segments", []) for step in (seg.get("trajectory") or {}).get("steps", [])
            for call in step.get("model_calls", [])
        ]
        worst = max([x for x in slow if x], default=0)
        return worst >= MIN_SLOW_MODEL_MS, f"slowest model call in the run: {worst:,.0f} ms"
    if fault == "slow_tool":
        slow = [(c["tool_name"], _seconds(c)) for c in story.get("tool_calls", []) if (_seconds(c) or 0) >= MIN_SLOW_TOOL_S]
        return bool(slow), f"slow tool calls: {slow}" if slow else "no tool call took long in this run's record"
    if fault in ("desk_kill", "desk_kill_in_refund"):
        resumed = [t for kind, t in texts if kind == "run_resumed"]
        if not resumed:
            return False, "no run_resumed event"
        detail = resumed[0]
        if fault == "desk_kill_in_refund":
            refunds = [c for c in story.get("tool_calls", []) if c["tool_name"] == "issue_refund"]
            detail += f"; issue_refund after the resume: {[(c['state'], c['outcome']) for c in refunds]}"
        return True, detail
    for _, text in texts:
        if pattern.search(text):
            return True, text[:300]
    return False, "; ".join(t[:120] for _, t in texts if "error" in t.lower())[:300] or "no event or error names the cause"


async def fetch_bundle(desk: Desk, run: dict) -> dict:
    events, _ = await desk.quiet("GET", "/telemetry/events", params={"run_id": run["run_id"], "limit": 500}, timeout=60)
    story, _ = await desk.quiet("GET", f"/runs/{run['run_id']}/trajectory", timeout=60)
    record, _ = await desk.quiet("GET", f"/runs/{run['run_id']}", timeout=60)
    events = events["events"] if isinstance(events, dict) else (events or [])
    return {"run": record or run, "events": events, "trajectory": story}


async def jaeger_check(jaeger: str, run: dict, pattern: str, wait: float = 45.0) -> dict:
    """Is the run's trace in Jaeger, and does its span carry the cause?"""
    wanted = [(t, jaeger_trace_id(t)) for t in run.get("trace_ids") or []]
    regex = re.compile(pattern, re.IGNORECASE)
    found: dict[str, dict] = {}
    deadline = time.time() + wait
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            for trace_id, jaeger_id in wanted:
                if trace_id in found:
                    continue
                try:
                    response = await client.get(f"{jaeger}/api/traces/{jaeger_id}")
                except httpx.HTTPError:
                    continue
                if response.status_code != 200:
                    continue
                data = (response.json().get("data") or [None])[0]
                if data:
                    blob = json.dumps(data["spans"])
                    found[trace_id] = {"spans": len(data["spans"]), "names": sorted({s["operationName"] for s in data["spans"]})[:12],
                                       "carries_cause": bool(regex.search(blob)),
                                       "error_spans": sum(1 for s in data["spans"] if any(t["key"] == "error" or t["key"] == "error.message" for t in s["tags"]))}
            if len(found) == len(wanted) or time.time() > deadline:
                break
            await asyncio.sleep(3)
    return {"segments_in_run": len(wanted), "segments_in_jaeger": len(found), "traces": found,
            "shows": any(v["carries_cause"] for v in found.values()), "any_trace": bool(found)}


async def prometheus_signals(desk: Desk) -> dict:
    """Which of the signals an operator needs does ``/prometheus`` have?"""
    response = await desk.client.get("/prometheus")
    names = sorted({line.split()[0].split("{")[0] for line in response.text.splitlines() if line and not line.startswith("#")})
    # The HTTP counters are not run signals: one per route, and a request.
    candidates = [n for n in names if not n.startswith("omniserve_requests_") and not n.startswith("omniserve_request_duration")]
    found = {}
    for key, (label, pattern) in SIGNALS.items():
        found[key] = {"what": label, "metrics": [n for n in candidates if re.search(pattern, n)]}
    return {"all_metrics": names, "signals": found}


def pick_candidates(fault: str, round_result: dict) -> list[dict]:
    """The runs of the round most likely to carry the fault, worst first."""
    runs = round_result.get("affected_runs", [])
    rank = {"failed": 0, "timeout": 0, "completed": 1}
    ordered = sorted(runs, key=lambda r: (rank.get(r["status"], 2), not r.get("resumed_by_operator"), r["run_id"]))
    if fault in ("desk_kill", "desk_kill_in_refund", "postgres_restart", "postgres_kill"):
        ordered = sorted(runs, key=lambda r: (not r.get("resumed_by_operator"), rank.get(r["status"], 2)))
    return ordered[:15]


async def check_fault(desk: Desk, jaeger: str, round_result: dict, metrics: dict) -> dict:
    fault = round_result["fault"]
    runs = pick_candidates(fault, round_result)
    signal = FAULT_SIGNAL.get(fault, "runs")
    signal_view = metrics["signals"][signal]
    out = {"fault": fault, "describe": round_result.get("describe"), "round": round_result["round"],
           "affected_runs": round_result.get("affected_run_count", 0), "metric_signal": signal,
           "metric_signal_what": signal_view["what"], "metric_signal_present": signal_view["metrics"]}
    if not runs:
        out.update(verdict="N/A", why="the fault touched no run", trace_explains=None, jaeger_shows=None, run_id=None)
        return out
    chosen, chosen_bundle, evidence, explained = None, None, "", False
    for run in runs:
        bundle = await fetch_bundle(desk, run)
        ok, text = explain(fault, bundle)
        if chosen is None or (ok and not explained):
            chosen, chosen_bundle, evidence, explained = run, bundle, text, ok
        if ok:
            break
    jaeger_result = await jaeger_check(jaeger, chosen_bundle["run"], CAUSES[fault])
    out.update(
        run_id=chosen["run_id"], run_status=chosen_bundle["run"].get("status"), trace_ids=chosen_bundle["run"].get("trace_ids"),
        trace_explains=explained, evidence=evidence, jaeger=jaeger_result, jaeger_shows=jaeger_result["shows"],
        candidates_tried=runs.index(chosen) + 1,
    )
    gaps = []
    if not explained:
        gaps.append("the run's trace does not name the cause")
    if not jaeger_result["any_trace"]:
        gaps.append("no trace of this run reached Jaeger")
    elif not jaeger_result["shows"]:
        gaps.append("Jaeger has the trace but its spans do not carry the cause")
    elif jaeger_result["segments_in_jaeger"] < jaeger_result["segments_in_run"]:
        gaps.append(f"only {jaeger_result['segments_in_jaeger']} of the run's {jaeger_result['segments_in_run']} trace segments reached Jaeger "
                    "(a trace is exported when it ends: the segment the process died in never ended)")
    if not signal_view["metrics"]:
        gaps.append(f"/prometheus has no metric for {signal_view['what']}")
    if re.search(r"run_resumed", evidence or ""):
        # The event says a run resumed, and which trace it continues. It does
        # not say why: nothing in the trace names a lapsed lease, a dead
        # process, or how long the run sat orphaned.
        gaps.append("run_resumed names no cause: nothing in the trace says the lease lapsed, that the process died, "
                    "or for how long the run was orphaned")
    out["gaps"] = gaps
    # The verdict is about explaining the fault from the trace and seeing it in
    # Jaeger; a missing metric and a lost pre-kill segment are listed as gaps.
    out["verdict"] = "PASS" if explained and jaeger_result["shows"] else "GAP"
    return out


def render(result: dict) -> str:
    lines = ["# Support desk observability checklist", "",
             f"Checked {datetime.fromtimestamp(result['t'], timezone.utc):%Y-%m-%d %H:%M} UTC against Jaeger at {result['jaeger']}. "
             "PASS: the run's trace names the cause and the span is in Jaeger. GAP: it does not. N/A: the fault touched no run.", "",
             "| Fault | Verdict | Run | Trace says | In Jaeger | Metric that would show it |", "|---|---|---|---|---|---|"]
    for r in result["faults"]:
        if r["verdict"] == "N/A":
            lines.append(f"| {r['fault']} | **N/A** | none touched | | | {r['metric_signal_what']}: "
                         f"{', '.join(r['metric_signal_present']) or 'no metric'} |")
            continue
        j = r["jaeger"]
        lines.append(
            f"| {r['fault']} | **{r['verdict']}** | `{r['run_id']}` ({r['run_status']}) | {str(r['evidence'])[:110].replace('|', '/')} | "
            f"{j['segments_in_jaeger']}/{j['segments_in_run']} segments{', cause shown' if r['jaeger_shows'] else ''} | "
            f"{r['metric_signal_what']}: {', '.join(r['metric_signal_present']) or 'no metric'} |")
    lines += ["", "## Gaps", ""]
    any_gap = False
    for r in result["faults"]:
        for gap in r.get("gaps", []):
            lines.append(f"- **{r['fault']}**: {gap}")
            any_gap = True
        if r["verdict"] == "N/A":
            lines.append(f"- **{r['fault']}**: touched no run, and no signal records that it happened")
            any_gap = True
    if not any_gap:
        lines.append("None.")
    lines += ["", "## Signals in `/prometheus`", ""]
    for key, view in result["prometheus"]["signals"].items():
        lines.append(f"- {view['what']}: {', '.join(view['metrics']) if view['metrics'] else '**none**'}")
    lines.append(f"\nAll metric names served: {', '.join(result['prometheus']['all_metrics'])}")
    lines.append("")
    return "\n".join(lines)


def arguments() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", action="store_true", help="inject each fault once (the chaos harness), then check")
    p.add_argument("--chaos-result", help="check the rounds of a chaos result.json instead")
    p.add_argument("--faults", help="comma-separated faults for --run (default: all)")
    p.add_argument("--users", type=int, default=10)
    p.add_argument("--warmup", default="15", help="seconds of load before each fault (--run)")
    p.add_argument("--after", default="20", help="seconds of load after each fault (--run)")
    p.add_argument("--jaeger-url", default=os.environ.get("DESK_JAEGER_URL", "http://127.0.0.1:16686"))
    p.add_argument("--base-url", default=os.environ.get("DESK_URL", "http://127.0.0.1:8800"))
    p.add_argument("--fake-url", default=os.environ.get("DESK_FAKE_URL_PUBLIC", "http://127.0.0.1:9000"))
    p.add_argument("--token", default=os.environ.get("OMNICOREAGENT_SERVE_AUTH_TOKEN", "change-me"))
    p.add_argument("--project", default=os.environ.get("DESK_PROJECT", "support-desk"))
    p.add_argument("--out")
    return p.parse_args()


async def main() -> int:
    args = arguments()
    if not args.run and not args.chaos_result:
        sys.exit("Give --run or --chaos-result.")
    desk = Desk(args.base_url, args.token)
    if args.chaos_result:
        chaos_result = json.loads(Path(args.chaos_result).read_text())
    else:
        names = args.faults.split(",") if args.faults else list(chaos.faults(chaos.arguments([])))
        chaos_args = chaos.arguments([
            "--rounds", str(len(names)), "--faults", ",".join(names), "--users", str(args.users),
            "--warmup", args.warmup, "--after", args.after,
            "--base-url", args.base_url, "--fake-url", args.fake_url, "--token", args.token, "--project", args.project,
        ])
        chaos_result = await chaos.run_chaos(chaos_args)
    metrics = await prometheus_signals(desk)
    faults = []
    seen = set()
    for round_result in chaos_result["rounds"]:
        if "harness_error" in round_result or round_result["fault"] in seen:
            continue
        seen.add(round_result["fault"])
        faults.append(await check_fault(desk, args.jaeger_url, round_result, metrics))
        print(f"[observe] {faults[-1]['fault']}: {faults[-1]['verdict']}", flush=True)
    await desk.close()
    result = {"t": time.time(), "jaeger": args.jaeger_url, "faults": faults, "prometheus": metrics,
              # What the chaos rounds behind the checklist found, so one file tells the whole story.
              "chaos_summary": chaos_result.get("summary"),
              "chaos_rounds": [{k: v for k, v in r.items() if k != "affected_runs"} for r in chaos_result["rounds"]]}
    out = Path(args.out or HERE / "results" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2, default=str))
    (out / "checklist.md").write_text(render(result))
    print(render(result))
    print(f"[observe] wrote {out}/result.json and checklist.md")
    return 0 if all(f["verdict"] in ("PASS", "N/A") for f in faults) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
