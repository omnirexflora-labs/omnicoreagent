"""A run whose model calls were not recorded gives no training record,
even when it paused and resumed.

The 0.5.0rc2 gate: under `capture: "default"` a paused-and-resumed run
yielded one record whose only step was the resumed one (no messages, tools
or response), though the docs say such runs are left out; there is nothing
in it to learn from.
"""

from __future__ import annotations

from omnicoreagent.core.runtime.omnicore_agent import _training_record


def _segment(steps):
    return {"run_id": "run_1", "trace_id": "t", "steps": steps, "totals": {}}


def test_a_resumed_default_capture_run_is_left_out():
    paused = _segment([{"step": 1, "model_calls": [
        {"purpose": "agent_turn", "request": {"message_count": 3}, "facts": {}}]}])
    resumed = _segment([{"step": 2, "resumed": True, "model_calls": [],
                         "tool_calls": [{"tool_call_id": "c1", "tool_name": "refund"}]}])

    assert _training_record([paused, resumed]) is None


def test_a_resumed_full_capture_run_keeps_its_resumed_step():
    paused = _segment([{"step": 1, "model_calls": [
        {"purpose": "agent_turn", "request": {"messages": [{"role": "user", "content": "hi"}]},
         "facts": {}}]}])
    resumed = _segment([{"step": 2, "resumed": True, "model_calls": [],
                         "tool_calls": [{"tool_call_id": "c1", "tool_name": "refund"}]}])

    record = _training_record([paused, resumed])
    assert [s["resumed"] for s in record["steps"]] == [False, True]
