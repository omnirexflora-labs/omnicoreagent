"""Per-run native schemas and unambiguous execution identities."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from omnicoreagent.core.model_protocol import ToolRequest
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.mcp_results import mcp_tool_definition

ALWAYS_VISIBLE_TOOL_NAMES = frozenset(
    {
        "tools_retriever",
        "spawn_subagents",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "insert_file",
        "delete_file",
        "move_file",
        "clear_files",
        "glob",
        "grep",
        "read_artifact",
        "tail_artifact",
        "search_artifact",
        "list_artifacts",
        "read_skill_file",
        "run_skill_script",
    }
)


@dataclass(frozen=True)
class ToolBinding:
    exposed_name: str
    name: str
    provider: str
    server: str | None
    description: str
    parameters: dict[str, Any]
    agent: Any = None
    # Safe to run again with the same arguments (crash recovery).
    idempotent: bool = False

    def definition(self) -> dict[str, Any]:
        """The tool as the model sees it.

        ``parameters`` is the catalog's own copy of the schema, made once when
        the catalog was built, and is handed out to be read, not changed: the
        model provider and the run record only read it, and copying it again
        for every step was a fifth of a request's serialization work.
        """
        return {
            "type": "function",
            "function": {
                "name": self.exposed_name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class NativeToolCatalog:
    def __init__(
        self, *, local_tools=None, mcp_tools=None, sub_agents=None, advanced=False
    ):
        candidates = []
        idempotent: dict[tuple, bool] = {("discovery", None, "tools_retriever"): True}
        if advanced:
            candidates.append(
                (
                    {
                        "name": "tools_retriever",
                        "description": "Find tools by describing the capability needed. Matching tools become available on the next turn.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string", "minLength": 1}},
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                    },
                    "discovery",
                    None,
                    None,
                )
            )
        if local_tools is not None:
            for tool in local_tools.get_available_tools():
                provider = (
                    local_tools.get_tool_provider(tool["name"])
                    if hasattr(local_tools, "get_tool_provider")
                    else "local"
                )
                candidates.append((tool, provider, None, None))
                idempotent[(provider, None, tool["name"])] = _local_idempotent(
                    local_tools, tool["name"], provider
                )
        for server, tools in (mcp_tools or {}).items():
            for tool in tools:
                definition = mcp_tool_definition(tool)
                candidates.append((definition, "mcp", server, None))
                idempotent[("mcp", server, definition["name"])] = _mcp_idempotent(tool)
        for agent in sub_agents or []:
            schema = ToolRegistry()._infer_schema(agent.run)
            runtime_parameters = {"session_id", "run_id", "on_event"}
            for parameter in runtime_parameters:
                schema["properties"].pop(parameter, None)
            schema["required"] = [
                name for name in schema["required"] if name not in runtime_parameters
            ]
            candidates.append(
                (
                    {
                        "name": f"delegate_{agent.name}",
                        "description": f"Delegate to {agent.name}. {getattr(agent, 'system_instruction', '')}",
                        "inputSchema": schema,
                    },
                    "subagent",
                    None,
                    agent,
                )
            )
        counts = Counter(str(tool["name"]).lower() for tool, _, _, _ in candidates)
        self.bindings: dict[str, ToolBinding] = {}
        for tool, provider, server, agent in candidates:
            name = str(tool["name"])
            exposed = name
            if (
                not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name)
                or counts[name.lower()] > 1
            ):
                identity = json.dumps([provider, server, name], ensure_ascii=False)
                suffix = hashlib.sha256(identity.encode()).hexdigest()[:16]
                prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:40]
                exposed = f"{prefix}_{suffix}"
            key = exposed.lower()
            if key in self.bindings:
                raise ValueError(f"Duplicate tool identity: {provider}/{server}/{name}")
            parameters = deepcopy(
                tool.get("inputSchema") or {"type": "object", "properties": {}}
            )
            if parameters.get("type", "object") != "object":
                raise ValueError(f"Tool {name} requires an object input schema")
            self.bindings[key] = ToolBinding(
                exposed,
                name,
                provider,
                server,
                tool.get("description") or "",
                parameters,
                agent,
                idempotent.get((provider, server, name), False),
            )
        self.visible = {
            key
            for key, binding in self.bindings.items()
            if not advanced
            or binding.name in ALWAYS_VISIBLE_TOOL_NAMES
            or binding.provider == "subagent"
        }

    def definitions(self) -> list[dict[str, Any]]:
        return [
            binding.definition()
            for key, binding in self.bindings.items()
            if key in self.visible
        ]

    def resolve(
        self, request: ToolRequest, *, arguments: dict[str, Any] | None = None
    ) -> tuple[ToolBinding, dict[str, Any]]:
        key = request.name.lower()
        if key not in self.visible:
            raise ValueError(
                f"Tool '{request.name}' is not available; discover it first if hidden"
            )
        binding = self.bindings[key]
        if arguments is None:
            arguments = request.decode_arguments()
        from jsonschema import Draft202012Validator

        error = next(
            Draft202012Validator(binding.parameters).iter_errors(arguments), None
        )
        if error is not None:
            raise ValueError(f"Invalid arguments for '{request.name}': {error.message}")
        return binding, arguments

    def discover(self, query: str) -> list[dict[str, Any]]:
        from omnicoreagent.core.tools.tool_search import (
            ToolDocument,
            ToolRetriever,
            tokenize,
        )

        documents = [
            ToolDocument(
                tool_name=key,
                tool_description=binding.description,
                tool_parameters=binding.parameters,
                mcp_server_name=binding.server or binding.provider,
                tokens=[],
                raw_text=f"{binding.name} {binding.description} {json.dumps(binding.parameters)}",
            )
            for key, binding in self.bindings.items()
            if binding.name not in ALWAYS_VISIBLE_TOOL_NAMES
        ]
        matches = ToolRetriever().bm25_score(tokenize(query), documents)
        selected = [
            doc.tool_name
            for score, doc in sorted(matches, key=lambda pair: pair[0], reverse=True)[
                :5
            ]
            if score > 0
        ]
        self.visible.update(selected)
        return [self.bindings[key].definition() for key in selected]


def _local_idempotent(local_tools: Any, name: str, provider: str) -> bool:
    """Built-in reads are idempotent; application tools declare it."""
    from omnicoreagent.governance.capabilities import (
        ARTIFACT_READ_TOOLS,
        WORKSPACE_READ_TOOLS,
    )

    if provider == "workspace" and name in WORKSPACE_READ_TOOLS:
        return True
    if provider == "artifact" and name in ARTIFACT_READ_TOOLS:
        return True
    if provider == "skill" and name == "read_skill_file":
        return True
    is_idempotent = getattr(local_tools, "is_idempotent", None)
    return bool(is_idempotent(name)) if callable(is_idempotent) else False


def _mcp_idempotent(tool: Any) -> bool:
    """An MCP tool declares it with the spec's readOnlyHint or idempotentHint."""
    annotations = getattr(tool, "annotations", None)
    if annotations is None and isinstance(tool, dict):
        annotations = tool.get("annotations")
    if annotations is None:
        return False
    if isinstance(annotations, dict):
        return bool(annotations.get("readOnlyHint") or annotations.get("idempotentHint"))
    return bool(
        getattr(annotations, "readOnlyHint", False) or getattr(annotations, "idempotentHint", False)
    )
