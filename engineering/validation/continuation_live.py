"""Thinking-model tool loops with real providers (or the local fakes).

Runs a two-step tool loop per provider with a thinking model, then a second
run in the same session from stored history, in `run()` and `stream()` mode,
and checks the trace: continuation data was recorded (counts and digest) and
no signature or API key was stored.

    PYTHONPATH=src .venv/bin/python engineering/validation/continuation_live.py --env-file <.env>
    PYTHONPATH=src .venv/bin/python engineering/validation/continuation_live.py --fake

Live mode runs every provider whose key is set (`ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `OPENROUTER_API_KEY`) and skips the others. Models can be
overridden with `CONTINUATION_ANTHROPIC_MODEL`, `CONTINUATION_GEMINI_MODEL`, and
`CONTINUATION_OPENROUTER_MODEL`. Keys are read at run time and never printed.
`--fake` runs the same checks against the fake servers in
`tests/fixtures/continuation_providers.py`, with no network.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = {
    "anthropic": ("ANTHROPIC_API_KEY", "CONTINUATION_ANTHROPIC_MODEL", "claude-sonnet-4-5"),
    "gemini": ("GEMINI_API_KEY", "CONTINUATION_GEMINI_MODEL", "gemini-3-pro-preview"),
    "openrouter": (
        "OPENROUTER_API_KEY",
        "CONTINUATION_OPENROUTER_MODEL",
        "anthropic/claude-sonnet-4.5",
    ),
}


def _load_env(env_file: str | None) -> dict[str, str]:
    values = dict(os.environ)
    if env_file:
        for line in Path(env_file).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return values


async def _check(provider: str, model: str, key: str, mode: str) -> dict[str, Any]:
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    tools = ToolRegistry()

    @tools.register_tool("lookup_order", description="Look up an order's status by its ID.")
    def lookup_order(order_id: str) -> dict:
        return {"order_id": order_id, "status": "shipped", "carrier": "DHL"}

    agent = OmniCoreAgent(
        name=f"continuation-{provider}",
        system_instruction="Always use lookup_order before answering. Answer in one sentence.",
        model_config={
            "provider": provider,
            "model": model,
            "api_key": key,
            "reasoning_effort": "low",
        },
        local_tools=tools,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, "max_steps": 4},
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    session = f"continuation-{provider}-{mode}"
    answers, traces = [], []
    for query in ("What is the status of order A-17?", "Which carrier has order A-17?"):
        if mode == "run":
            result = await agent.run(query, session_id=session)
            answers.append(result["response"])
            trace_id = result["trace_id"]
        else:
            text = []
            async for event in agent.stream(query, session_id=session):
                delta = getattr(event, "text", None) or (
                    event.get("text") if isinstance(event, dict) else None
                )
                if delta:
                    text.append(delta)
            answers.append("".join(text))
            latest = await agent.get_latest_trace(session)
            trace_id = latest["trace_id"] if isinstance(latest, dict) else latest.trace_id
        traces.append(await agent.telemetry_store.get_trace(trace_id))

    failures = []
    dump = json.dumps([trace.model_dump() for trace in traces], default=str)
    if key in dump:
        failures.append("API key stored in the trace")
    for index, trace in enumerate(traces, start=1):
        if trace.status != "completed":
            failures.append(f"run {index} ended {trace.status}")
    continuation = [
        event.metadata["model_call"]["continuation"]
        for trace in traces
        for event in trace.events
        if event.event_type == "model_response"
        and "continuation" in (event.metadata.get("model_call") or {})
    ]
    if not continuation:
        failures.append("no continuation data recorded (did the model think?)")
    return {
        "provider": provider,
        "model": model,
        "mode": mode,
        "answers": answers,
        "continuation": continuation,
        "failures": failures,
    }


async def main(args) -> int:
    env = _load_env(args.env_file)
    fake = None
    if args.fake:
        sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
        from continuation_providers import ContinuationProviders

        fake = ContinuationProviders()
        os.environ.update(
            ANTHROPIC_API_BASE=fake.base,
            GEMINI_API_BASE=fake.base,
            OPENROUTER_API_BASE=f"{fake.base}/api/v1",
        )
    results = []
    with tempfile.TemporaryDirectory(prefix="omni-continuation-") as workspace:
        os.environ["OMNICOREAGENT_WORKSPACE_DIR"] = workspace
        try:
            for provider, (key_name, model_name, default_model) in PROVIDERS.items():
                key = "fake-key" if fake else env.get(key_name)
                if not key:
                    print(f"{provider}: skipped ({key_name} not set)")
                    continue
                model = env.get(model_name) or default_model
                for mode in ("run", "stream"):
                    results.append(await _check(provider, model, key, mode))
        finally:
            if fake:
                fake.close()
    failed = False
    for result in results:
        status = "FAIL " + "; ".join(result["failures"]) if result["failures"] else "ok"
        print(f"{result['provider']} {result['mode']} ({result['model']}): {status}")
        print(f"  continuation: {result['continuation']}")
        failed = failed or bool(result["failures"])
    return 1 if failed or not results else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file")
    parser.add_argument("--fake", action="store_true", help="use the local fake providers")
    sys.exit(asyncio.run(main(parser.parse_args())))
