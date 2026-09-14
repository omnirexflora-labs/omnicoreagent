"""Opt-in live OpenAI validation; all tools and workspace data are synthetic.

PYTHONPATH=src python engineering/validation/live_native_runtime.py --env-file .env
No credentials or raw exception messages are included in the JSON report.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import aclosing, nullcontext
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import socket
import time
import tempfile

from dotenv import dotenv_values

from omnicoreagent import (
    BackgroundAgentManager,
    MemoryRouter,
    OmniCoreAgent,
    OmniServe,
    OmniServeConfig,
    ToolRegistry,
)
from omnicoreagent.core.agents.llm_response import normalize_model_turn


class ObservedConnection:
    """Observe actual requests/turns without replacing the provider."""

    def __init__(self, connection):
        self.connection = connection
        self.requests = []
        self.tool_schemas = []
        self.turns = []
        self.closed_streams = 0
        self.completed_streams = 0

    async def llm_call(self, messages, tools=None):
        self.requests.append([self.connection.to_dict(m) for m in deepcopy(messages)])
        self.tool_schemas.append(deepcopy(tools))
        response = await self.connection.llm_call(messages, tools=tools)
        self.turns.append(normalize_model_turn(response))
        return response

    async def llm_stream(self, messages, tools=None):
        self.requests.append([self.connection.to_dict(m) for m in deepcopy(messages)])
        self.tool_schemas.append(deepcopy(tools))
        try:
            async with aclosing(
                self.connection.llm_stream(messages, tools=tools)
            ) as stream:
                async for event in stream:
                    if event["type"] == "turn_complete":
                        self.turns.append(event["turn"])
                        self.completed_streams += 1
                    yield event
        finally:
            self.closed_streams += 1


async def make_agent(
    model, *, name="live", tools=None, children=None, config=None, instruction=None
):
    agent = OmniCoreAgent(
        name=name,
        system_instruction=instruction
        or "Follow the synthetic validation task exactly. Use the specified tools; do not invent tool results.",
        model_config={
            "provider": "openai",
            "model": model,
            "max_tokens": 2500,
            "reasoning_effort": "none",
        },
        local_tools=tools,
        sub_agents=children,
        memory_router=MemoryRouter("in_memory"),
        agent_config={
            "guardrail_mode": "off",
            "max_steps": 8,
            "tool_call_timeout": 30,
            **(config or {}),
        },
    )
    await agent.initialize()
    observed = ObservedConnection(agent.llm_connection)
    agent.llm_connection = observed
    return agent, observed


def assert_success(result):
    assert result.get("status") == "success", result.get(
        "termination_reason", "unsuccessful"
    )
    assert result.get("response"), "empty response"


async def validate(model, scenarios=None):
    rows = []

    async def check(name, fn):
        start = time.monotonic()
        try:
            details = await asyncio.wait_for(fn(), 120)
            row = {
                "scenario": name,
                "status": "passed",
                "seconds": round(time.monotonic() - start, 2),
                **(details or {}),
            }
        except Exception as exc:
            row = {
                "scenario": name,
                "status": "failed",
                "seconds": round(time.monotonic() - start, 2),
                "error_type": type(exc).__name__,
            }
            # Assertion messages here are authored checks or synthetic outcome reasons.
            if isinstance(exc, AssertionError):
                row["check"] = str(exc)[:180]
        rows.append(row)
        print(json.dumps(row), flush=True)

    async def text():
        agent, observed = await make_agent(model)
        try:
            result = await agent.run(
                "Reply with exactly: <sample>XML is data</sample>", session_id="text"
            )
            assert_success(result)
            assert "<sample>XML is data</sample>" in result["response"], (
                "XML text was changed"
            )
            assert not observed.turns[-1].tool_calls
            return {
                "provider_turns": len(observed.turns),
                "usage_tokens": result["metric"].total_tokens,
            }
        finally:
            await agent.cleanup()

    async def tools_and_history():
        registry = ToolRegistry()
        effects = []

        @registry.register_tool(name="issue_receipt")
        async def receipt(code: str, quantity: int):
            effects.append((code, quantity))
            return {
                "receipt": f"RECEIPT_{code}_{quantity}",
                "literal_xml": "<sample>payload</sample>",
            }

        agent, observed = await make_agent(model, tools=registry)
        try:
            result = await agent.run(
                'In one batch call issue_receipt twice, independently: code "001" quantity 7 and code "002" quantity 8. Then report both returned receipt IDs. Do not call again.',
                session_id="tools",
            )
            assert_success(result)
            assert sorted(effects) == [("001", 7), ("002", 8)], (
                "wrong tool arguments/effects"
            )
            batch = next(t for t in observed.turns if t.tool_calls)
            assert len(batch.tool_calls) == 2, "model did not request a native batch"
            records = await agent.memory_router.get_messages("tools", "live")
            results = [r for r in records if r["role"] == "tool"]
            assert {r["metadata"]["tool_call_id"] for r in results} == {
                c.id for c in batch.tool_calls
            }, "history IDs mismatch"
            result = await agent.run(
                "Without tools, repeat the two receipt IDs from our previous exchange.",
                session_id="tools",
            )
            assert_success(result)
            assert all(
                x in result["response"] for x in ["RECEIPT_001_7", "RECEIPT_002_8"]
            ), "continued session lost results"
            assert len(effects) == 2, "continued session unnecessarily executed tools"
            return {
                "provider_turns": len(observed.turns),
                "native_calls": 2,
                "stored_tool_results": len(results),
            }
        finally:
            await agent.cleanup()

    async def failed_tools():
        registry = ToolRegistry()

        @registry.register_tool(name="failed_probe")
        async def failed():
            raise ValueError("synthetic tool failure")

        @registry.register_tool(name="slow_probe")
        async def slow():
            await asyncio.sleep(3)
            return "late"

        agent, observed = await make_agent(
            model, tools=registry, config={"tool_call_timeout": 2}
        )
        try:
            result = await agent.run(
                "Call failed_probe and slow_probe exactly once each in the same batch. Both are expected to fail. Then summarize their errors without retrying.",
                session_id="errors",
            )
            assert_success(result)
            records = await agent.memory_router.get_messages("errors", "live")
            results = [json.loads(r["content"]) for r in records if r["role"] == "tool"]
            assert len(results) == 2 and all(r["status"] == "error" for r in results), (
                "missing correlated failures"
            )
            assert any("timed out" in str(r) for r in results), (
                "timeout not represented"
            )
            return {
                "provider_turns": len(observed.turns),
                "recoverable_errors": len(results),
            }
        finally:
            await agent.cleanup()

    async def stream():
        registry = ToolRegistry()
        effects = []

        @registry.register_tool(name="stream_receipt")
        async def receipt(code: str):
            effects.append(code)
            return "STREAM_RECEIPT_001"

        agent, observed = await make_agent(model, tools=registry)
        try:
            deltas = []
            first = None
            start = time.monotonic()
            async with aclosing(
                agent.stream(
                    'Call stream_receipt with code "001", then explain in about 80 words that the receipt was obtained. Include its exact ID.',
                    session_id="stream",
                )
            ) as events:
                async for event in events:
                    if event["type"] == "text_delta":
                        deltas.append(event["text"])
                        if first is None:
                            first = time.monotonic() - start
                            assert observed.closed_streams < len(observed.requests), (
                                "first text only arrived after provider close"
                            )
                        # The queue may still contain trailing text after the
                        # provider closes. Early first delivery proves streaming;
                        # requiring every delta before closure is a timing race.
                    elif event["type"] == "complete":
                        result = event
                    else:
                        raise AssertionError("terminal stream error")
            assert_success(result)
            assert effects == ["001"], "stream tool did not execute exactly once"
            assert deltas and "STREAM_RECEIPT_001" in result["response"], (
                "missing streamed answer"
            )
            return {
                "text_deltas": len(deltas),
                "first_delta_seconds": round(first, 2),
                "provider_turns": len(observed.turns),
            }
        finally:
            await agent.cleanup()

    async def cancellation():
        agent, observed = await make_agent(model)
        try:
            async with aclosing(
                agent.stream(
                    "Write a detailed 1000-word fictional description of a quiet garden."
                )
            ) as stream:
                event = await anext(stream)
                assert event["type"] == "text_delta", "no live text before cancellation"
            assert observed.closed_streams == 1, "provider stream was not closed"
            assert observed.completed_streams == 0, (
                "provider finished before cancellation check"
            )
            return {"closed_provider_streams": observed.closed_streams}
        finally:
            await agent.cleanup()

    async def child():
        worker, child_observed = await make_agent(model, name="worker")
        parent, observed = await make_agent(model, name="parent", children=[worker])
        try:
            events = []
            async with aclosing(
                parent.stream(
                    "Use delegate_worker once, asking the worker to reply with CHILD_READY. Then report that exact reply.",
                    session_id="delegate",
                )
            ) as stream:
                events = [event async for event in stream]
            assert_success(events[-1])
            assert any(
                e.get("agent_name") == "worker" and e["type"] == "text_delta"
                for e in events
            ), "child did not stream under its own identity"
            assert child_observed.turns, "child never called provider"
            return {
                "parent_turns": len(observed.turns),
                "child_turns": len(child_observed.turns),
            }
        finally:
            await parent.cleanup()
            await worker.cleanup()

    async def http_sse():
        import httpx
        import uvicorn

        agent, observed = await make_agent(model, name="http")
        app = OmniServe(
            agent, OmniServeConfig(background_enabled=False, request_timeout=90)
        ).app
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="critical", access_log=False)
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError("server failed to start")
                await asyncio.sleep(0.01)
            names = []
            first = None
            start = time.monotonic()
            async with httpx.AsyncClient(timeout=90) as client:
                async with client.stream(
                    "POST",
                    f"http://127.0.0.1:{port}/run",
                    json={
                        "query": "Write an 800-word fictional description of a quiet garden.",
                        "session_id": "http",
                    },
                ) as response:
                    assert response.status_code == 200, "SSE HTTP request failed"
                    async for line in response.aiter_lines():
                        if line.startswith("event: "):
                            names.append(line[7:])
                        if (
                            names
                            and names[-1] == "text_delta"
                            and line.startswith("data: ")
                            and first is None
                        ):
                            first = time.monotonic() - start
                            assert observed.completed_streams == 0, (
                                "HTTP buffered text until model completion"
                            )
                        if (
                            names
                            and names[-1] == "complete"
                            and line.startswith("data: ")
                        ):
                            assert_success(json.loads(line[6:]))
            assert "text_delta" in names and names.count("complete") == 1, (
                "SSE missing deltas or terminal"
            )
            return {
                "text_deltas": names.count("text_delta"),
                "first_delta_seconds": round(first, 2),
                "http_status": 200,
            }
        finally:
            server.should_exit = True
            await task
            sock.close()
            await agent.cleanup()

    async def deep_workspace():
        with tempfile.TemporaryDirectory(prefix="omni-live-deep-") as directory:
            agent, observed = await make_agent(
                model,
                name="deep",
                config={
                    "enable_subagents": True,
                    "tool_call_timeout": 60,
                    "workspace_config": {"workspace_dir": directory},
                },
            )
            try:
                async with aclosing(
                    agent.stream(
                        "Call spawn_subagents exactly once with one worker named writer, role synthetic validation writer, task write exactly DEEP_READY to worker.md, output_path worker.md. After the worker finishes, use read_file to inspect worker.md yourself and report its content.",
                        session_id="deep",
                    )
                ) as stream:
                    events = [event async for event in stream]
                assert_success(events[-1])
                assert "DEEP_READY" in events[-1]["response"], (
                    "parent lost child output"
                )
                calls = [
                    call.name for turn in observed.turns for call in turn.tool_calls
                ]
                assert calls.count("spawn_subagents") == 1 and "read_file" in calls, (
                    "missing delegation or parent workspace read"
                )
                assert any(
                    e.get("agent_name") == "subagent_writer"
                    and e["type"] == "text_delta"
                    for e in events
                ), "dynamic child stream missing"
                files = list(Path(directory).rglob("worker.md"))
                assert files and any(
                    "DEEP_READY" in file.read_text() for file in files
                ), "child did not write output"
                return {
                    "parent_turns": len(observed.turns),
                    "workspace_output_verified": True,
                    "dynamic_child_stream_verified": True,
                }
            finally:
                await agent.cleanup()

    async def background():
        from omnicoreagent.core.workspace.manager import Workspace

        with tempfile.TemporaryDirectory(prefix="omni-live-background-") as directory:
            workspace = Workspace.from_config(workspace_dir=directory).ensure()
            registry = ToolRegistry()
            effects = []

            @registry.register_tool(name="background_receipt")
            async def receipt():
                effects.append("called")
                return "BACKGROUND_READY"

            agent, observed = await make_agent(model, name="background", tools=registry)
            manager = BackgroundAgentManager(
                workspace=workspace, worker_id="live-validation"
            )
            try:
                await manager.register_agent("background", agent)
                await manager.register_task(
                    task_id="live-task",
                    agent_id="background",
                    query="Call background_receipt exactly once and report its exact result.",
                    schedule={"type": "manual"},
                    timeout_seconds=90,
                    retry_policy={"max_retries": 0},
                )
                run = await manager.run_now("live-task", wait=True)
                assert run.status.value == "completed", (
                    "background run did not complete"
                )
                assert effects == ["called"], "background tool did not execute once"
                assert "BACKGROUND_READY" in run.result_preview, (
                    "background lost agent result"
                )
                events = await manager.get_run_events(run.run_id)
                assert events, "background events missing"
                return {
                    "provider_turns": len(observed.turns),
                    "background_status": run.status.value,
                    "events": len(events),
                }
            finally:
                await manager.shutdown()
                await agent.cleanup()

    async def parallel_business_results():
        registry = ToolRegistry()
        started = set()
        both_started = asyncio.Event()

        async def rendezvous(name):
            started.add(name)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 5)

        @registry.register_tool(name="measurement")
        async def measurement():
            await rendezvous("measurement")
            return {"data": 0, "unit": "kg", "message": "ZERO_KG_VERIFIED"}

        @registry.register_tool(name="reference_unit")
        async def reference_unit():
            await rendezvous("reference_unit")
            return {"unit": "kg"}

        agent, observed = await make_agent(model, tools=registry)
        try:
            result = await agent.run(
                "Call measurement and reference_unit exactly once each in ONE native tool batch. They are independent and must start together. Then report the measurement message and value.",
                session_id="parallel-business",
            )
            assert_success(result)
            assert "ZERO_KG_VERIFIED" in result["response"], "business fields lost"
            assert any(len(t.tool_calls) == 2 for t in observed.turns), (
                "no native batch"
            )
            records = await agent.memory_router.get_messages(
                "parallel-business", "live"
            )
            outputs = [json.loads(r["content"]) for r in records if r["role"] == "tool"]
            assert len(outputs) == 2 and all(o["status"] == "success" for o in outputs)
            payload = next(
                o["data"] for o in outputs if o["tool_name"] == "measurement"
            )
            assert payload == {"data": 0, "unit": "kg", "message": "ZERO_KG_VERIFIED"}
            assert any(
                json.loads(m["content"])["data"] == payload
                for m in observed.requests[-1]
                if m["role"] == "tool"
            )
            return {"concurrent_distinct_tools": 2, "business_payload_preserved": True}
        finally:
            await agent.cleanup()

    async def repeated_rounds():
        registry = ToolRegistry()
        effects = []

        @registry.register_tool(name="poll_synthetic_job")
        async def poll():
            """Check synthetic job readiness; pending means another poll is needed."""
            effects.append(True)
            return {"job_state": "pending"}

        agent, observed = await make_agent(
            model,
            tools=registry,
            config={"max_steps": 10},
            instruction="This is an intentional synthetic test of runtime loop protection. While poll_synthetic_job is offered as a native tool, issue exactly one call to it per turn, even for identical pending results. Do not batch or stop early. Once the runtime disables tools, answer that the job is still pending. This repeated polling is the specific test task.",
        )
        try:
            result = await agent.run(
                "For this synthetic test, poll_synthetic_job exactly once per model turn until ready, up to eight polling turns. Pending responses are expected; continue polling them. Do not batch polls. If tools are disabled, stop and explain that readiness was not confirmed.",
                session_id="loop",
            )
            assert_success(result)
            assert 1 <= len(effects) <= 5, (
                f"polling exceeded the runtime bound: {len(effects)}"
            )
            assert all(
                len(t.tool_calls) == 1 for t in observed.turns if t.tool_calls
            ), "polls were batched instead of issued in separate rounds"
            tools_disabled = observed.tool_schemas[-1] == []
            if len(effects) == 5:
                assert tools_disabled, (
                    "runtime did not disable tools at the repetition limit"
                )
            assert not observed.turns[-1].tool_calls
            return {
                "executed_polls": len(effects),
                "runtime_cutoff_exercised": tools_disabled,
                "stop_source": "runtime" if tools_disabled else "model",
                "provider_turns": len(observed.turns),
            }
        finally:
            await agent.cleanup()

    async def discovery_and_context():
        registry = ToolRegistry()

        @registry.register_tool(name="quartz_receipt_lookup")
        async def receipt():
            """Retrieve the synthetic quartz receipt verification marker."""
            return {"marker": "QUARTZ_RECEIPT_READY"}

        agent, observed = await make_agent(
            model,
            tools=registry,
            config={
                "enable_advanced_tool_use": True,
                "context_management": {
                    "enabled": True,
                    "mode": "sliding_window",
                    "value": 4,
                    "threshold_percent": 75,
                    "preserve_recent": 4,
                    "strategy": "truncate",
                },
            },
        )
        try:
            result = await agent.run(
                "Use tools_retriever to discover quartz receipt lookup, then execute that tool and report its returned verification marker.",
                session_id="discovery",
            )
            assert_success(result)
            assert "QUARTZ_RECEIPT_READY" in result["response"]
            assert not any(
                t["function"]["name"] == "quartz_receipt_lookup"
                for t in observed.tool_schemas[0]
            )
            names = [c.name for t in observed.turns for c in t.tool_calls]
            assert "tools_retriever" in names and "quartz_receipt_lookup" in names
            assert agent.agent.context_manager._management_count > 0
            for messages in observed.requests:
                expected = {c["id"] for m in messages for c in m.get("tool_calls", [])}
                actual = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
                assert expected == actual, "context split a native interaction"
            return {
                "discovery_used": True,
                "context_compressions": agent.agent.context_manager._management_count,
            }
        finally:
            await agent.cleanup()

    async def offload_readback():
        with tempfile.TemporaryDirectory(prefix="omni-live-offload-") as directory:
            registry = ToolRegistry()

            @registry.register_tool(name="bulk_report")
            async def bulk_report():
                return (
                    "unimportant evidence\n" * 500
                    + "audit_marker: OFFLOAD_READBACK_READY\n"
                )

            agent, observed = await make_agent(
                model,
                tools=registry,
                config={
                    "workspace_config": {"workspace_dir": directory},
                    "tool_offload": {
                        "enabled": True,
                        "threshold_bytes": 300,
                        "threshold_tokens": 100,
                        "max_preview_lines": 2,
                        "max_preview_tokens": 30,
                    },
                },
            )
            try:
                result = await agent.run(
                    "Call bulk_report once. If it is offloaded, use the artifact tools to find the line starting audit_marker and return its value.",
                    session_id="offload",
                )
                assert_success(result)
                assert "OFFLOAD_READBACK_READY" in result["response"]
                records = await agent.memory_router.get_messages("offload", "live")
                assert any(
                    "OFFLOADED" in r["content"] for r in records if r["role"] == "tool"
                )
                names = [c.name for t in observed.turns for c in t.tool_calls]
                assert any(
                    n in names
                    for n in ("read_artifact", "search_artifact", "tail_artifact")
                )
                return {"offloaded": True, "read_back_with_native_tool": True}
            finally:
                await agent.cleanup()

    async def skill_readback():
        previous = Path.cwd()
        with tempfile.TemporaryDirectory(prefix="omni-live-skill-") as directory:
            root = Path(directory) / ".agents/skills/audit-check"
            root.mkdir(parents=True)
            (root / "SKILL.md").write_text(
                "---\nname: audit-check\ndescription: Read the synthetic verification marker from evidence.txt.\n---\nUse read_skill_file to read evidence.txt and return its marker.\n"
            )
            (root / "evidence.txt").write_text("SKILL_READBACK_READY\n")
            agent = None
            try:
                os.chdir(directory)
                agent, observed = await make_agent(
                    model, config={"enable_agent_skills": True}
                )
                result = await agent.run(
                    "Use the audit-check skill. Read its SKILL.md and evidence.txt with read_skill_file and report the marker.",
                    session_id="skill",
                )
                assert_success(result)
                assert "SKILL_READBACK_READY" in result["response"]
                names = [c.name for t in observed.turns for c in t.tool_calls]
                assert names.count("read_skill_file") >= 2
                return {"native_skill_reads": names.count("read_skill_file")}
            finally:
                if agent:
                    await agent.cleanup()
                os.chdir(previous)

    cases = [
        ("parallel_business_payloads", parallel_business_results),
        ("bounded_native_polling", repeated_rounds),
        ("advanced_discovery_and_context", discovery_and_context),
        ("offload_native_readback", offload_readback),
        ("skill_native_readback", skill_readback),
        ("xml_task_content", text),
        ("native_batch_and_continued_session", tools_and_history),
        ("tool_failure_and_timeout", failed_tools),
        ("public_stream_and_tool_continuation", stream),
        ("stream_cancellation", cancellation),
        ("configured_child_stream", child),
        ("http_sse_live_delivery", http_sse),
        ("dynamic_deep_agent_workspace", deep_workspace),
        ("background_native_tool_run", background),
    ]
    if set(scenarios or ()) - {name for name, _ in cases}:
        raise ValueError("Unknown validation scenario")
    for name, fn in cases:
        if not scenarios or name in scenarios:
            await check(name, fn)
    return {
        "model": model,
        "scenarios": rows,
        "passed": sum(r["status"] == "passed" for r in rows),
        "failed": sum(r["status"] == "failed" for r in rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--require-litellm", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--scenario", action="append")
    args = parser.parse_args()
    if args.env_file:
        key = dotenv_values(args.env_file).get("LLM_API_KEY")
        if key:
            os.environ["LLM_API_KEY"] = key
    if not os.getenv("LLM_API_KEY"):
        parser.error("LLM_API_KEY is required")
    logging.disable(logging.CRITICAL)
    adapter_context = nullcontext(None)
    if args.require_litellm:
        from provider_observation import observe_litellm

        adapter_context = observe_litellm()
    with adapter_context as counters:
        report = asyncio.run(validate(args.model, args.scenario))
    report["adapter"] = "production_litellm" if args.require_litellm else "production"
    if counters is not None:
        report["adapter_counters"] = counters
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "model": args.model,
                "passed": report["passed"],
                "failed": report["failed"],
            }
        )
    )
    raise SystemExit(1 if report["failed"] else 0)


if __name__ == "__main__":
    main()
