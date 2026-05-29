from __future__ import annotations

import posixpath
import re
from typing import Any
from urllib.parse import unquote, urlparse

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
BACKGROUND_TASK_ACTIONS = frozenset({"create", "update", "delete", "pause", "resume"})
BACKGROUND_RUN_ACTIONS = frozenset({"start", "cancel"})
FILESYSTEM_ACTIONS = frozenset({"read", "write", "delete"})
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head"})


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
            risk_level=tool_risk_level(
                tool_name=tool_name, tool_provider=tool_provider
            ),
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
    capability = "mcp.server.start" if transport == "stdio" else "mcp.server.connect"
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


def network_capability_descriptor(
    *,
    method: str,
    host: str | None = None,
    credentialed: bool = False,
) -> CapabilityDescriptor:
    method = _normalize_http_method(method)
    return CapabilityDescriptor(
        capability=f"network.http.{method}",
        provider="network",
        execution_surface="network",
        descriptor_source="app_code",
        descriptor_trust="trusted",
        risk_level=_network_risk_level(method=method, credentialed=credentialed),
        data_classes=["credential"] if credentialed else [],
        metadata={
            "method": method,
            "host": _normalize_host(host) if host else None,
            "credentialed": credentialed,
        },
    )


def network_authority_request(
    *,
    method: str,
    host: str | None = None,
    url: str | None = None,
    actor: str = "agent",
    credentialed: bool = False,
    data_classes: list[str] | None = None,
) -> AuthorityRequest:
    method = _normalize_http_method(method)
    resolved_host = _normalize_host(host or _host_from_url(url))
    classes = list(data_classes or [])
    if credentialed and "credential" not in classes:
        classes.append("credential")
    return AuthorityRequest(
        capability=f"network.http.{method}",
        actor=actor,
        provider="network",
        execution_surface="network",
        target=AuthorityTarget(host=resolved_host),
        risk_level=_network_risk_level(method=method, credentialed=credentialed),
        data_classes=classes,
        method=method,
        host=resolved_host,
        metadata={
            "method": method,
            "host": resolved_host,
            "url": _redacted_url(url),
            "credentialed": credentialed,
        },
    )


def package_install_authority_request(
    *,
    package: str,
    manager: str = "pip",
    host: str | None = None,
    actor: str = "agent",
) -> AuthorityRequest:
    package = _normalize_package_name(package)
    manager = _normalize_package_manager(manager)
    resolved_host = _normalize_host(host) if host else None
    return AuthorityRequest(
        capability="package.install",
        actor=actor,
        provider="package",
        execution_surface="network",
        target=AuthorityTarget(resource=package, host=resolved_host),
        risk_level="high",
        method=manager,
        host=resolved_host,
        metadata={
            "package": package,
            "manager": manager,
            "host": resolved_host,
        },
    )


def package_install_capability_descriptor(
    *,
    package: str | None = None,
    manager: str = "pip",
    host: str | None = None,
) -> CapabilityDescriptor:
    normalized_package = _normalize_package_name(package) if package else None
    manager = _normalize_package_manager(manager)
    return CapabilityDescriptor(
        capability="package.install",
        provider="package",
        execution_surface="network",
        descriptor_source="app_code",
        descriptor_trust="trusted",
        risk_level="high",
        metadata={
            "package": normalized_package,
            "manager": manager,
            "host": _normalize_host(host) if host else None,
        },
    )


def filesystem_capability_descriptor(
    *,
    action: str,
    path: str | None = None,
) -> CapabilityDescriptor:
    action = _normalize_filesystem_action(action)
    return CapabilityDescriptor(
        capability=f"filesystem.{action}",
        provider="filesystem",
        execution_surface="filesystem",
        descriptor_source="app_code",
        descriptor_trust="trusted",
        risk_level=_filesystem_risk_level(action),
        metadata={
            "action": action,
            "path": _normalize_filesystem_path(path) if path else None,
        },
    )


def filesystem_authority_request(
    *,
    action: str,
    path: str,
    actor: str = "agent",
    sandbox_id: str | None = None,
) -> AuthorityRequest:
    action = _normalize_filesystem_action(action)
    safe_path = _normalize_filesystem_path(path)
    return AuthorityRequest(
        capability=f"filesystem.{action}",
        actor=actor,
        provider="filesystem",
        execution_surface="filesystem",
        target=AuthorityTarget(path=safe_path, resource=sandbox_id),
        risk_level=_filesystem_risk_level(action),
        metadata={
            "action": action,
            "path": safe_path,
            "sandbox_id": sandbox_id,
        },
    )


def secret_capability_descriptor(
    *,
    secret_ref: str,
    brokered: bool = True,
) -> CapabilityDescriptor:
    secret_ref = _normalize_secret_ref(secret_ref)
    return CapabilityDescriptor(
        capability="secret.use" if brokered else "secret.read",
        provider="secret",
        execution_surface="secret_broker" if brokered else "secret",
        descriptor_source="app_code",
        descriptor_trust="trusted",
        risk_level="medium" if brokered else "critical",
        data_classes=[] if brokered else ["credential"],
        metadata={"secret_ref": secret_ref, "brokered": brokered},
    )


def secret_authority_request(
    *,
    secret_ref: str,
    purpose: str,
    actor: str = "agent",
    brokered: bool = True,
) -> AuthorityRequest:
    secret_ref = _normalize_secret_ref(secret_ref)
    purpose = str(purpose or "").strip()
    if not purpose:
        raise ValueError("secret purpose is required")
    return AuthorityRequest(
        capability="secret.use" if brokered else "secret.read",
        actor=actor,
        provider="secret",
        execution_surface="secret_broker" if brokered else "secret",
        target=AuthorityTarget(resource=secret_ref),
        risk_level="medium" if brokered else "critical",
        data_classes=[] if brokered else ["credential"],
        metadata={
            "secret_ref": secret_ref,
            "purpose": purpose,
            "brokered": brokered,
        },
    )


def subagent_spawn_authority_requests(
    *,
    subagent_specs: list[dict[str, Any]],
    tool_names: list[str] | None = None,
    mcp_servers: list[str] | None = None,
    memory_scope: str | None = None,
    budget: dict[str, Any] | None = None,
    deadline: str | None = None,
    actor: str = "agent",
) -> list[AuthorityRequest]:
    return [
        AuthorityRequest(
            capability="subagent.spawn",
            actor=actor,
            provider="subagent",
            execution_surface="subagent",
            target=AuthorityTarget(
                resource=str(spec.get("name") or f"subagent_{index}"),
                path=spec.get("output_path"),
            ),
            risk_level="medium",
            metadata={
                "subagent_name": spec.get("name") or f"subagent_{index}",
                "role": spec.get("role"),
                "task": spec.get("task"),
                "output_path": spec.get("output_path"),
                "task_length": len(str(spec.get("task") or "")),
                "tool_names": list(tool_names or []),
                "mcp_servers": list(mcp_servers or []),
                "workspace_scope": spec.get("output_path"),
                "memory_scope": memory_scope,
                "budget": dict(budget or {}),
                "deadline": deadline,
            },
        )
        for index, spec in enumerate(subagent_specs)
    ]


def background_task_authority_request(
    *,
    task: Any,
    action: str = "create",
    actor: str = "background_manager",
) -> AuthorityRequest:
    if action not in BACKGROUND_TASK_ACTIONS:
        supported = ", ".join(sorted(BACKGROUND_TASK_ACTIONS))
        raise ValueError(
            f"Unsupported background task action: {action}. Supported: {supported}"
        )
    return AuthorityRequest(
        capability=f"background.task.{action}",
        actor=actor,
        provider="background",
        execution_surface="background",
        target=AuthorityTarget(resource=task.task_id),
        risk_level="medium" if action in {"create", "update"} else "low",
        metadata={
            "action": action,
            "task_id": task.task_id,
            "agent_id": task.agent_id,
            "schedule_type": getattr(task.schedule.type, "value", task.schedule.type),
            "enabled": task.enabled,
        },
    )


def background_run_authority_request(
    *,
    action: str,
    run: Any = None,
    task: Any = None,
    actor: str = "background_manager",
) -> AuthorityRequest:
    if action not in BACKGROUND_RUN_ACTIONS:
        supported = ", ".join(sorted(BACKGROUND_RUN_ACTIONS))
        raise ValueError(
            f"Unsupported background run action: {action}. Supported: {supported}"
        )
    task_id = getattr(run, "task_id", None) or getattr(task, "task_id", None)
    agent_id = getattr(run, "agent_id", None) or getattr(task, "agent_id", None)
    return AuthorityRequest(
        capability=f"background.run.{action}",
        actor=actor,
        provider="background",
        execution_surface="background",
        target=AuthorityTarget(resource=task_id),
        risk_level="medium" if action == "start" else "low",
        metadata={
            "action": action,
            "run_id": getattr(run, "run_id", None),
            "task_id": task_id,
            "agent_id": agent_id,
            "trigger_type": getattr(getattr(run, "trigger_type", None), "value", None),
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


def _normalize_http_method(method: str) -> str:
    normalized = str(method or "").lower().strip()
    if normalized not in HTTP_METHODS:
        supported = ", ".join(sorted(HTTP_METHODS))
        raise ValueError(f"Unsupported HTTP method: {method}. Supported: {supported}")
    return normalized


def _normalize_host(host: str | None) -> str:
    normalized = str(host or "").strip().lower()
    if not normalized:
        raise ValueError("network host is required")
    if "/" in normalized or "\\" in normalized:
        raise ValueError(f"Invalid network host: {host}")
    parsed = urlparse(f"//{normalized}")
    if parsed.username or parsed.password:
        raise ValueError(f"Invalid network host: {host}")
    if parsed.port is not None:
        raise ValueError(f"Network host must not include a port: {host}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Invalid network host: {host}")
    return hostname.lower()


def _host_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(str(url))
    if parsed.username or parsed.password:
        raise ValueError("network URL must not contain embedded credentials")
    if parsed.port is not None:
        raise ValueError("network URL must not include an explicit port")
    return parsed.hostname


def _network_risk_level(*, method: str, credentialed: bool) -> str:
    if credentialed:
        return "high"
    if method in {"post", "put", "patch", "delete"}:
        return "medium"
    return "low"


def _normalize_filesystem_action(action: str) -> str:
    normalized = str(action or "").lower().strip()
    if normalized not in FILESYSTEM_ACTIONS:
        supported = ", ".join(sorted(FILESYSTEM_ACTIONS))
        raise ValueError(
            f"Unsupported filesystem action: {action}. Supported: {supported}"
        )
    return normalized


def _normalize_filesystem_path(path: str | None) -> str:
    raw = unquote(str(path or "").strip())
    if not raw:
        raise ValueError("filesystem path is required")
    if "\x00" in raw:
        raise ValueError("filesystem path contains a null byte")
    parts = [part for part in raw.replace("\\", "/").split("/") if part]
    if ".." in parts:
        raise ValueError(f"filesystem path escapes allowed scope: {path}")
    normalized = posixpath.normpath(raw.replace("\\", "/"))
    if normalized == ".":
        raise ValueError("filesystem path is required")
    return normalized


def _normalize_package_name(package: str | None) -> str:
    normalized = str(package or "").strip()
    if not normalized:
        raise ValueError("package is required")
    if re.search(r"[;&|`$<>]", normalized):
        raise ValueError("package must be an identity, not command text")
    if "\x00" in normalized or any(char.isspace() for char in normalized):
        raise ValueError("package contains invalid control characters")
    return normalized


def _normalize_package_manager(manager: str) -> str:
    normalized = str(manager or "").strip().lower()
    if not normalized:
        raise ValueError("package manager is required")
    if any(char.isspace() for char in normalized) or any(
        char in normalized for char in "/\\\x00"
    ):
        raise ValueError(f"Invalid package manager: {manager}")
    return normalized


def _filesystem_risk_level(action: str) -> str:
    if action == "delete":
        return "high"
    if action == "write":
        return "medium"
    return "low"


def _normalize_secret_ref(secret_ref: str) -> str:
    normalized = str(secret_ref or "").strip()
    if not normalized:
        raise ValueError("secret_ref is required")
    if any(char.isspace() for char in normalized):
        raise ValueError("secret_ref must not contain whitespace")
    if "\x00" in normalized:
        raise ValueError("secret_ref must not contain null bytes")
    parts = [part for part in normalized.replace("\\", "/").split("/") if part]
    if ".." in parts:
        raise ValueError(f"secret_ref escapes allowed scope: {secret_ref}")
    return normalized


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
        return parsed.path
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}{parsed.path}"
