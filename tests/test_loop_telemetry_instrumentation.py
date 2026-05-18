import json

import pytest

from omnicoreagent.core.agents.base import BaseReactAgent
from omnicoreagent.core.telemetry import (
    InMemoryTelemetryStore,
    TelemetryActor,
    TelemetryRecorder,
)
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry


@pytest.mark.asyncio
async def test_react_loop_records_model_step_and_parallel_tool_telemetry():
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-loop",
        run_id="run-loop",
        session_id="session-loop",
        actor=TelemetryActor(type="agent", name="loop-agent"),
    )

    registry = ToolRegistry()
    history = []

    @registry.register_tool(
        name="alpha",
        inputSchema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        description="Return alpha value.",
    )
    async def alpha(value: str):
        return {"status": "success", "data": f"alpha:{value}"}

    @registry.register_tool(
        name="beta",
        inputSchema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        description="Return beta value.",
    )
    async def beta(value: str):
        return {"status": "success", "data": f"beta:{value}"}

    class FakeLLMConnection:
        def __init__(self):
            self.calls = 0

        async def llm_call(self, messages):
            self.calls += 1
            if self.calls == 1:
                return """
<thought>Need both tools.</thought>
<tool_calls>
  <tool_call>
    <tool_name>alpha</tool_name>
    <parameters><value>one</value></parameters>
  </tool_call>
  <tool_call>
    <tool_name>beta</tool_name>
    <parameters><value>two</value></parameters>
  </tool_call>
</tool_calls>
"""
            return "<final_answer>done</final_answer>"

    async def add_message_to_history(role, content, metadata=None, session_id=None):
        history.append(
            {
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "session_id": session_id,
            }
        )

    async def message_history(agent_name, session_id):
        return history

    agent = BaseReactAgent(
        agent_name="loop-agent",
        max_steps=5,
        tool_call_timeout=10,
    )

    result = await agent.run(
        system_prompt="system",
        query="run both tools",
        llm_connection=FakeLLMConnection(),
        add_message_to_history=add_message_to_history,
        message_history=message_history,
        session_id="session-loop",
        local_tools=registry,
        telemetry_recorder=recorder,
    )
    await recorder.end_trace(output={"answer": result["answer"]})

    trace = await store.get_trace(context.trace_id)
    span_kinds = [span.kind for span in trace.spans]
    event_types = [event.event_type for event in trace.events]

    assert result["answer"] == "done"
    assert span_kinds.count("agent.step") == 2
    assert span_kinds.count("model.call") == 2
    assert span_kinds.count("tool.batch") == 1
    assert span_kinds.count("tool.call") == 2
    assert "agent_step" in event_types
    assert event_types.count("model_call") == 2
    assert event_types.count("model_response") == 2
    assert "tool_batch_start" in event_types
    assert "tool_batch_end" in event_types
    assert event_types.count("tool_call") == 2
    assert event_types.count("tool_result") == 2
    assert "observation_pipeline_end" in event_types

    tool_spans = [span for span in trace.spans if span.kind == "tool.call"]
    assert {span.name for span in tool_spans} == {"alpha", "beta"}
    assert {span.output["tool_name"] for span in tool_spans} == {"alpha", "beta"}

    tool_batch = next(span for span in trace.spans if span.kind == "tool.batch")
    assert json.loads(json.dumps(tool_batch.input["tool_batch_args"])) == [
        {"value": "one"},
        {"value": "two"},
    ]
