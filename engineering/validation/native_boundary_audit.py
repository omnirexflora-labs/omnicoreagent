"""Offline diagnostics against the installed SDK, not hand-built MCP mocks.

Run from the repository root: python engineering/validation/native_boundary_audit.py
Failures are findings, not a passing regression gate. No model/API calls are made.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import sys

from mcp import types

from omnicoreagent.core.agents.llm_response import normalize_model_turn
from omnicoreagent.core.agents.loop_detection import NativeLoopDetector, ToolInteraction
from omnicoreagent.core.runtime.harness_tools import available_tools
from omnicoreagent.core.tools.native_catalog import NativeToolCatalog
from omnicoreagent.core.tools.tool_executor import ToolExecutor
from omnicoreagent.mcp_clients_connection.client import MCPClient
from omnicoreagent.mcp_clients_connection.transports import open_server_transport


async def main():
    findings = {}
    tool = types.Tool(name="remote_echo", input_schema={"type": "object"})
    for name, operation in {
        "mcp_native_catalog": lambda: NativeToolCatalog(
            mcp_tools={"audit": [tool]}
        ).definitions(),
        "mcp_public_tool_listing": lambda: available_tools(
            type("Client", (), {"available_tools": {"audit": [tool]}})(), None
        ),
    }.items():
        try:
            operation()
            findings[name] = {"passed": True}
        except Exception as exc:
            findings[name] = {"passed": False, "error": str(exc)}

    result = ToolExecutor(None)._normalize_result(
        "remote_echo",
        {},
        types.CallToolResult(
            content=[types.TextContent(type="text", text="synthetic failure")],
            structured_content={"code": 42},
            is_error=True,
        ),
    )
    findings["mcp_error_and_structured_result"] = {
        "passed": result["status"] == "error"
        and isinstance(result["data"], dict)
        and result["data"].get("structuredContent") == {"code": 42},
        "actual": result,
    }
    server = {
        "name": "native-audit",
        "command": sys.executable,
        "args": [
            str(Path(__file__).with_name("fixtures") / "native_audit_mcp_server.py")
        ],
    }
    client = MCPClient()
    try:
        outcome = await client._connect_to_single_server(server, server["name"])
        findings["real_stdio_connection"] = {
            "passed": bool(client.available_tools),
            "outcome": outcome,
        }
    finally:
        await client.cleanup()
    try:
        async with AsyncExitStack() as stack:
            await open_server_transport(
                stack=stack,
                server={
                    "transport_type": "streamable_http",
                    "url": "http://127.0.0.1:1/mcp",
                },
            )
        findings["http_transport_api"] = {"passed": True}
    except Exception as exc:
        findings["http_transport_api"] = {"passed": False, "error": str(exc)}

    business = {"data": "value", "unit": "kg"}
    result = ToolExecutor(None)._normalize_result("business", {}, business)
    findings["business_dictionary_preserved"] = {
        "passed": result["data"] == business,
        "actual": result["data"],
    }
    response = {
        "choices": [
            {
                "message": {
                    "content": "ok",
                    "provider_specific_fields": {
                        "synthetic_continuation": "must-survive"
                    },
                }
            }
        ]
    }
    normalized = normalize_model_turn(response)
    findings["provider_specific_fields_retained"] = {
        "passed": "provider_specific_fields" in normalized.assistant_message()
    }
    detector = NativeLoopDetector()
    interactions = [
        ToolInteraction("mcp", str(i), "lookup", {}, "same") for i in range(5)
    ]
    for item in interactions:
        detector.record_round([item])
    findings["same_name_servers_have_distinct_loop_identity"] = {
        "passed": not detector.is_looping(),
        "note": "Five distinct server identities; transport-independent detector probe.",
    }
    findings.update(execution_boundaries())
    print(json.dumps(findings, indent=2))


def execution_boundaries() -> dict:
    """The execution lanes: what each is governed as, and what runs code."""
    from omnicoreagent.core.tools.code_mode import CodeModeConfig
    from omnicoreagent.governance.capabilities import tool_authority_requests
    from omnicoreagent.sandbox import build_sandbox_runtime, registered_sandbox_providers

    findings: dict = {}
    expected = {
        ("execute", "sandbox"): ("sandbox.execute", "sandbox", "high"),
        ("run_code", "code"): ("code.run", "code", "medium"),
        ("run_skill_script", "skill"): ("skill.script.run", "host", "high"),
        ("write_file", "workspace"): ("workspace.files.write", "workspace", "medium"),
        ("some_tool", "mcp"): ("tool.mcp.call", "mcp", "low"),
        ("my_tool", "local"): ("tool.local.call", "tool", "low"),
    }
    labels = {}
    for (tool_name, provider), wanted in expected.items():
        (request,) = tool_authority_requests(
            tool_name=tool_name, tool_args={"path": "x"}, tool_provider=provider
        )
        actual = (request.capability, request.execution_surface, request.risk_level)
        labels[f"{provider}:{tool_name}"] = {"expected": list(wanted), "actual": list(actual)}
    findings["execution_capability_labels"] = {
        "passed": all(item["expected"] == item["actual"] for item in labels.values()),
        "labels": labels,
    }

    providers = {}
    for name in sorted(registered_sandbox_providers()):
        options = {"base_url": "https://example.invalid"} if name == "http" else {}
        try:
            runtime = build_sandbox_runtime({"provider": name, "options": options})
            providers[name] = {
                "supports_execution": bool(runtime.supports_execution),
                "supports_required_sandbox": bool(runtime.supports_required_sandbox),
            }
        except Exception as exc:  # an optional extra is not installed here
            providers[name] = {"unavailable": type(exc).__name__}
    findings["sandbox_providers"] = {
        "passed": providers.get("none", {}).get("supports_execution") is False
        and all(
            item.get("supports_execution", True)
            for name, item in providers.items()
            if name != "none" and "unavailable" not in item
        ),
        "providers": providers,
    }

    findings["code_mode_defaults_off_and_bounded"] = {
        "passed": CodeModeConfig.from_value(None).enabled is False
        and CodeModeConfig.from_value({"enabled": True}).max_tool_calls > 0,
    }
    return findings


if __name__ == "__main__":
    asyncio.run(main())
