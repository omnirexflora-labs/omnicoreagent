import asyncio
import inspect
from collections.abc import Callable
from typing import Any


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        inputSchema: dict[str, Any],
        function: Callable,
    ):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema
        self.function = function
        self.provider = "local"
        self.internal_provider = False
        self.is_async = asyncio.iscoroutinefunction(function)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.inputSchema,
            "function": self.function,
            "provider": self.provider,
            "internal_provider": self.internal_provider,
        }

    async def execute(self, parameters: dict[str, Any]) -> Any:
        """Execute the tool with extracted parameters"""
        sig = inspect.signature(self.function)
        func_params = {}

        for param_name, param in sig.parameters.items():
            if param_name in parameters:
                func_params[param_name] = parameters[param_name]
            elif param.default is not inspect.Parameter.empty:
                func_params[param_name] = param.default
            else:
                raise ValueError(f"Missing required parameter: {param_name}")

        if self.is_async:
            return await self.function(**func_params)
        else:
            return self.function(**func_params)

    def __repr__(self):
        return f"<Tool name={self.name} async={self.is_async}>"


class ToolRegistry:
    """Registry for local tools that can be executed by agents."""

    def __init__(self):
        self.tools: dict[str, Tool] = {}
        self._internal_tool_providers: dict[str, str] = {}

    def __str__(self):
        """Return a readable string representation of the ToolRegistry."""
        tool_count = len(self.tools)
        tool_names = list(self.tools.keys())
        return f"ToolRegistry({tool_count} tools: {', '.join(tool_names[:3])}{'...' if tool_count > 3 else ''})"

    def __repr__(self):
        """Return a detailed representation of the ToolRegistry."""
        return self.__str__()

    def register(self, tool: Tool | Any):
        """Register a pre-constructed Tool object or a tool wrapper with get_tool()."""
        if hasattr(tool, "get_tool") and callable(tool.get_tool):
            tool = tool.get_tool()

        if not isinstance(tool, Tool):
            raise TypeError(
                "Expected Tool object or object with get_tool() method, "
                f"got {type(tool)}"
            )

        tool.provider = "local"
        tool.internal_provider = False
        self.tools[tool.name.lower()] = tool
        self._internal_tool_providers.pop(tool.name.lower(), None)

    def merge(self, other_registry: "ToolRegistry"):
        """Merge tools from another registry into this one."""
        for tool in other_registry.list_tools():
            self.register(tool)

    def register_tool(
        self,
        name: str | None = None,
        inputSchema: dict[str, Any] | None = None,
        description: str = "",
    ):
        def decorator(func: Callable):
            tool_name = name or func.__name__.lower()

            final_description = description or (
                func.__doc__ or "No description provided."
            )

            final_schema = inputSchema or self._infer_schema(func)

            tool = Tool(
                name=tool_name,
                description=final_description.strip(),
                inputSchema=final_schema,
                function=func,
            )
            self.tools[tool_name.lower()] = tool
            self._internal_tool_providers.pop(tool_name.lower(), None)
            return func

        return decorator

    def get_tool(self, name: str) -> Tool | None:
        return self.tools.get(name.lower())

    def mark_internal_tool_provider(self, name: str, provider: str) -> None:
        tool = self.get_tool(name)
        if tool is None:
            raise ValueError(f"Tool '{name}' not found")
        normalized_provider = provider.strip().lower()
        if normalized_provider not in {"workspace", "artifact"}:
            raise ValueError(
                "internal tool provider must be either 'workspace' or 'artifact'"
            )
        self._internal_tool_providers[name.lower()] = normalized_provider
        tool.provider = normalized_provider
        tool.internal_provider = True

    def get_tool_provider(self, name: str) -> str:
        return self._internal_tool_providers.get(name.lower(), "local")

    def list_tools(self) -> list[Tool]:
        return list(self.tools.values())

    def get_available_tools(self) -> list[dict[str, Any]]:
        """Get list of available tools for OmniCoreAgent"""
        tools = []
        for tool in self.list_tools():
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.inputSchema,
                    "type": "local",
                }
            )
        return tools

    async def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> Any:
        """Execute a tool by name with parameters"""
        tool = self.get_tool(tool_name)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found")

        return await tool.execute(parameters)

    def _infer_schema(self, func: Callable) -> dict[str, Any]:
        sig = inspect.signature(func)
        props = {}
        required = []

        docstring = func.__doc__ or ""
        doc_lines = [line.strip() for line in docstring.split("\n") if ":" in line]

        param_docs = {}
        for line in doc_lines:
            parts = line.split(":", 1)
            if len(parts) == 2:
                param_docs[parts[0].strip()] = parts[1].strip()

        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue

            param_type = (
                param.annotation
                if param.annotation is not inspect.Parameter.empty
                else str
            )
            schema = {"type": self._map_type(param_type)}

            if param_name in param_docs:
                schema["description"] = param_docs[param_name]

            props[param_name] = schema

            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        return {
            "type": "object",
            "properties": props,
            "required": required,
            "additionalProperties": False,
        }

    def _map_type(self, typ: Any) -> str:
        type_map = {
            int: "integer",
            float: "number",
            str: "string",
            bool: "boolean",
            list: "array",
            dict: "object",
        }
        return type_map.get(typ, "string")
