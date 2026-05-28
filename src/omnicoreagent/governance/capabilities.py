from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from omnicoreagent.core.workspace.paths import (
    WORKSPACE_FILE_PATH_PREFIXES,
    normalize_workspace_path,
)
from omnicoreagent.governance.models import (
    AuthorityRequest,
    AuthorityTarget,
    CapabilityDescriptor,
)


WORKSPACE_READ_TOOLS = frozenset({"ls", "read_file", "glob", "grep"})
WORKSPACE_WRITE_TOOLS = frozenset({"write_file", "edit_file", "insert_file"})
WORKSPACE_DELETE_TOOLS = frozenset({"delete_file", "clear_files"})
WORKSPACE_MOVE_TOOLS = frozenset({"move_file"})
ARTIFACT_READ_TOOLS = frozenset(
    {"read_artifact", "tail_artifact", "search_artifact", "list_artifacts"}
)
MCP_SERVER_TRANSPORTS = frozenset({"stdio", "sse", "streamable_http"})


def tool_capability_descriptor(
    *,
    tool_name: str,
    tool_provider: str = "local",
    tool_server: str | None = None,
) -> CapabilityDescriptor:
    capability = tool_capability_name(tool_name=tool_name, tool_provider=tool_provider)
    return CapabilityDescriptor(
        capability=capability,
        provider=tool_provider,
        execution_surface=_execution_surface(tool_provider),
        descriptor_source=(
            "mcp_schema"
            if tool_provider == "mcp"
            else "builtin"
            if tool_provider in {"workspace", "artifact"}
            else "app_code"
        ),
        descriptor_trust="trusted" if tool_provider != "mcp" else "untrusted",
        risk_level=tool_risk_level(tool_name=tool_name, tool_provider=tool_provider),
        metadata={
            "tool_name": tool_name,
            "tool_provider": tool_provider,
            "tool_server": tool_server,
        },
    )


def tool_authority_request(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    tool_provider: str = "local",
    tool_server: str | None = None,
    actor: str = "agent",
) -> AuthorityRequest:
    return tool_authority_requests(
        tool_name=tool_name,
        tool_args=tool_args,
        tool_provider=tool_provider,
        tool_server=tool_server,
        actor=actor,
    )[0]


def tool_authority_requests(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    tool_provider: str = "local",
    tool_server: str | None = None,
    actor: str = "agent",
) -> list[AuthorityRequest]:
    descriptor = tool_capability_descriptor(
        tool_name=tool_name,
        tool_provider=tool_provider,
        tool_server=tool_server,
    )
    targets = tool_authority_targets(
        tool_name=tool_name,
        tool_args=tool_args,
        tool_provider=tool_provider,
        tool_server=tool_server,
    )
    return [
        AuthorityRequest(
            capability=descriptor.capability,
            actor=actor,
            provider=descriptor.provider,
            execution_surface=descriptor.execution_surface,
            target=target,
            risk_level=tool_risk_level(tool_name=tool_name, tool_provider=tool_provider),
            mcp_server=tool_server if tool_provider == "mcp" else None,
            metadata={
                "tool_name": tool_name,
                "tool_provider": tool_provider,
                "tool_server": tool_server,
                "target_role": role,
            },
        )
        for role, target in targets
    ]


def tool_authority_targets(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    tool_provider: str,
    tool_server: str | None = None,
) -> list[tuple[str, AuthorityTarget]]:
    if tool_provider == "workspace":
        if tool_name == "move_file":
            return [
                (
                    "source",
                    AuthorityTarget(
                        path=_normalize_workspace_target(tool_args.get("old_path")),
                        tool_name=tool_name,
                    ),
                ),
                (
                    "destination",
                    AuthorityTarget(
                        path=_normalize_workspace_target(tool_args.get("new_path")),
                        tool_name=tool_name,
                    ),
                ),
            ]
        return [
            (
                "scope",
                AuthorityTarget(
                    path=_workspace_target_path(tool_name, tool_args),
                    tool_name=tool_name,
                ),
            )
        ]
    if tool_provider == "artifact":
        return [
            (
                "artifact",
                AuthorityTarget(
                    resource=tool_args.get("artifact_id"),
                    tool_name=tool_name,
                ),
            )
        ]
    if tool_provider == "mcp":
        return [
            (
                "mcp_tool",
                AuthorityTarget(tool_name=tool_name, mcp_server=tool_server),
            )
        ]
    return [("tool", AuthorityTarget(tool_name=tool_name))]


def tool_authority_target(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    tool_provider: str,
    tool_server: str | None = None,
) -> AuthorityTarget:
    return tool_authority_targets(
        tool_name=tool_name,
        tool_args=tool_args,
        tool_provider=tool_provider,
        tool_server=tool_server,
    )[0][1]


def tool_capability_name(*, tool_name: str, tool_provider: str) -> str:
    if tool_provider == "workspace":
        if tool_name in WORKSPACE_READ_TOOLS:
            return "workspace.files.read"
        if tool_name in WORKSPACE_WRITE_TOOLS:
            return "workspace.files.write"
        if tool_name in WORKSPACE_DELETE_TOOLS:
            if tool_name == "clear_files":
                return "workspace.files.clear"
            return "workspace.files.delete"
        if tool_name in WORKSPACE_MOVE_TOOLS:
            return "workspace.files.move"
        return "workspace.files.call"
    if tool_provider == "artifact":
        if tool_name in ARTIFACT_READ_TOOLS:
            return "workspace.artifacts.read"
        return "workspace.artifacts.call"
    if tool_provider == "mcp":
        return "tool.mcp.call"
    return "tool.local.call"


def mcp_server_authority_request(
    *,
    server: dict[str, Any],
    actor: str = "agent",
) -> AuthorityRequest:
    transport = str(server.get("transport_type", "stdio")).lower()
    if transport not in MCP_SERVER_TRANSPORTS:
        supported = ", ".join(sorted(MCP_SERVER_TRANSPORTS))
        raise ValueError(
            f"Unsupported MCP transport_type: {transport}. Supported: {supported}"
        )
    server_name = str(server.get("name") or "")
    host = _server_host(server)
    capability = (
        "mcp.server.start"
        if transport == "stdio"
        else "mcp.server.connect"
    )
    return AuthorityRequest(
        capability=capability,
        actor=actor,
        provider="mcp",
        execution_surface="mcp",
        target=AuthorityTarget(
            resource=server_name,
            host=host,
            mcp_server=server_name,
        ),
        risk_level="medium" if transport == "stdio" else "low",
        method=transport,
        host=host,
        mcp_server=server_name,
        metadata={
            "server_name": server_name,
            "requested_name": server.get("requested_name"),
            "transport_type": transport,
            "has_explicit_env": bool(server.get("env")),
            "url": _redacted_url(server.get("url")),
            "command": server.get("command") if transport == "stdio" else None,
        },
    )


def tool_risk_level(*, tool_name: str, tool_provider: str) -> str:
    if tool_provider == "workspace":
        if tool_name == "clear_files":
            return "critical"
        if tool_name in WORKSPACE_DELETE_TOOLS:
            return "high"
        if tool_name in WORKSPACE_WRITE_TOOLS or tool_name in WORKSPACE_MOVE_TOOLS:
            return "medium"
    return "low"


def _workspace_target_path(tool_name: str, tool_args: dict[str, Any]) -> str | None:
    if tool_name in {"grep", "glob"}:
        return _normalize_workspace_target(tool_args.get("path") or "")
    return _normalize_workspace_target(tool_args.get("path"))


def _normalize_workspace_target(path: Any) -> str | None:
    if path is None:
        return None
    return normalize_workspace_path(
        path,
        strip_prefixes=WORKSPACE_FILE_PATH_PREFIXES,
    )


def _execution_surface(tool_provider: str) -> str:
    if tool_provider == "workspace":
        return "workspace"
    if tool_provider == "artifact":
        return "artifact"
    if tool_provider == "mcp":
        return "mcp"
    return "tool"


def _server_host(server: dict[str, Any]) -> str | None:
    url = server.get("url")
    if not url:
        return None
    parsed = urlparse(str(url))
    return parsed.hostname


def _redacted_url(url: Any) -> str | None:
    if not url:
        return None
    parsed = urlparse(str(url))
    if not parsed.scheme or not parsed.netloc:
        return str(url)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}{parsed.path}"
