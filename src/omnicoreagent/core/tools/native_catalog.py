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
from omnicoreagent.core.tools.tool_prompt_renderer import ALWAYS_VISIBLE_TOOL_NAMES


@dataclass(frozen=True)
class ToolBinding:
    exposed_name: str
    name: str
    provider: str
    server: str | None
    description: str
    parameters: dict[str, Any]
    agent: Any = None

    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.exposed_name,
                "description": self.description,
                "parameters": deepcopy(self.parameters),
            },
        }


class NativeToolCatalog:
    def __init__(
        self, *, local_tools=None, mcp_tools=None, sub_agents=None, advanced=False
    ):
        candidates = []
        if local_tools is not None:
            for tool in local_tools.get_available_tools():
                provider = (
                    local_tools.get_tool_provider(tool["name"])
                    if hasattr(local_tools, "get_tool_provider")
                    else "local"
                )
                candidates.append((tool, provider, None, None))
        for server, tools in (mcp_tools or {}).items():
            for tool in tools:
                data = (
                    tool
                    if isinstance(tool, dict)
                    else {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.inputSchema,
                    }
                )
                candidates.append((data, "mcp", server, None))
        for agent in sub_agents or []:
            schema = ToolRegistry()._infer_schema(agent.run)
            schema["properties"].pop("session_id", None)
            schema["required"] = [
                name for name in schema["required"] if name != "session_id"
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

    def resolve(self, request: ToolRequest) -> tuple[ToolBinding, dict[str, Any]]:
        key = request.name.lower()
        if key not in self.visible:
            raise ValueError(
                f"Tool '{request.name}' is not available; discover it first if hidden"
            )
        binding = self.bindings[key]
        arguments = request.decode_arguments()
        from jsonschema import Draft202012Validator

        error = next(
            Draft202012Validator(binding.parameters).iter_errors(arguments), None
        )
        if error is not None:
            raise ValueError(f"Invalid arguments for '{request.name}': {error.message}")
        return binding, arguments

    def discover(self, query: str) -> list[dict[str, Any]]:
        from omnicoreagent.core.tools.advance_tools.advanced_tools_use import (
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
