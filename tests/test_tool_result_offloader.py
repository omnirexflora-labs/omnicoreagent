from omnicoreagent.core.workspace.artifacts import ToolResponseOffloader
from omnicoreagent.core.tools.tool_result_offloader import ToolResultOffloader
from omnicoreagent.core.types import ToolCallResult


def test_maybe_offload_result_replaces_large_regular_output(tmp_path):
    offloader = ToolResponseOffloader(
        config={"enabled": True, "threshold_bytes": 20, "threshold_tokens": 10000},
        base_dir=str(tmp_path),
    )
    handler = ToolResultOffloader(tool_offloader=offloader)
    result = {
        "tool_name": "search_docs",
        "args": {"query": "runtime"},
        "status": "success",
        "data": "x" * 80,
        "message": None,
    }
    processed = handler.maybe_offload_result(result=result, session_id="chat792")
    assert processed is result
    assert "[TOOL RESPONSE OFFLOADED]" in result["data"]
    assert "Tool: search_docs" in result["data"]
    assert offloader.get_stats()["offload_count"] == 1


def test_maybe_offload_result_keeps_artifact_tool_output_inline(tmp_path):
    offloader = ToolResponseOffloader(
        config={"enabled": True, "threshold_bytes": 20, "threshold_tokens": 10000},
        base_dir=str(tmp_path),
    )
    handler = ToolResultOffloader(tool_offloader=offloader)
    result = {
        "tool_name": "read_artifact",
        "tool_provider": "artifact",
        "args": {"artifact_id": "artifact_1"},
        "status": "success",
        "data": "x" * 80,
        "message": None,
    }
    processed = handler.maybe_offload_result(result=result, session_id="chat793")
    assert processed is result
    assert result["data"] == "x" * 80
    assert offloader.get_stats()["offload_count"] == 0


def test_maybe_offload_result_keeps_workspace_provider_output_inline(tmp_path):
    offloader = ToolResponseOffloader(
        config={"enabled": True, "threshold_bytes": 20, "threshold_tokens": 10000},
        base_dir=str(tmp_path),
    )
    handler = ToolResultOffloader(tool_offloader=offloader)
    result = {
        "tool_name": "read_file",
        "args": {"path": "notes.md"},
        "status": "success",
        "data": "x" * 80,
        "message": None,
    }
    tool_call_result = ToolCallResult(
        tool_executor=None,
        tool_name="read_file",
        tool_args={"path": "notes.md"},
        tool_provider="workspace",
    )
    processed = handler.maybe_offload_result(
        result=result, session_id="chat-workspace", tool_call_result=tool_call_result
    )
    assert processed is result
    assert result["data"] == "x" * 80
    assert offloader.get_stats()["offload_count"] == 0


def test_maybe_offload_result_offloads_app_tool_with_workspace_like_name(tmp_path):
    offloader = ToolResponseOffloader(
        config={"enabled": True, "threshold_bytes": 20, "threshold_tokens": 10000},
        base_dir=str(tmp_path),
    )
    handler = ToolResultOffloader(tool_offloader=offloader)
    result = {
        "tool_name": "read_file",
        "args": {"path": "notes.md"},
        "status": "success",
        "data": "x" * 80,
        "message": None,
    }
    tool_call_result = ToolCallResult(
        tool_executor=None,
        tool_name="read_file",
        tool_args={"path": "notes.md"},
        tool_provider="local",
    )
    processed = handler.maybe_offload_result(
        result=result,
        session_id="chat-local-read-file",
        tool_call_result=tool_call_result,
    )
    assert processed is result
    assert "[TOOL RESPONSE OFFLOADED]" in result["data"]
    assert offloader.get_stats()["offload_count"] == 1
