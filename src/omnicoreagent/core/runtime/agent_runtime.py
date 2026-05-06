from __future__ import annotations

from importlib import import_module
from typing import Any


_RUNTIME_EXPORTS = {
    "AdvanceToolsUse": (
        "omnicoreagent.core.tools.advance_tools.advanced_tools_use",
        "AdvanceToolsUse",
    ),
    "AgentConfig": ("omnicoreagent.core.runtime.config", "AgentConfig"),
    "DetectionConfig": ("omnicoreagent.core.guardrails", "DetectionConfig"),
    "EventRouter": ("omnicoreagent.core.events.event_router", "EventRouter"),
    "FAST_CONVERSATION_SUMMARY_PROMPT": (
        "omnicoreagent.core.system_prompts",
        "FAST_CONVERSATION_SUMMARY_PROMPT",
    ),
    "LLMConnection": ("omnicoreagent.core.llm", "LLMConnection"),
    "MemoryRouter": ("omnicoreagent.core.memory_store.memory_router", "MemoryRouter"),
    "OmniCoreAgentPromptBuilder": (
        "omnicoreagent.core.system_prompts",
        "OmniCoreAgentPromptBuilder",
    ),
    "PromptInjectionGuard": ("omnicoreagent.core.guardrails", "PromptInjectionGuard"),
    "REACT_AGENT_PROMPT": ("omnicoreagent.core.system_prompts", "REACT_AGENT_PROMPT"),
    "ReactAgent": ("omnicoreagent.core.agents.react_agent", "ReactAgent"),
    "SubagentFactory": ("omnicoreagent.core.subagents", "SubagentFactory"),
    "Usage": ("omnicoreagent.core.token_usage", "Usage"),
    "build_subagent_tools": ("omnicoreagent.core.subagents", "build_subagent_tools"),
    "logger": ("omnicoreagent.core.utils", "logger"),
    "normalize_agent_config": (
        "omnicoreagent.core.runtime.config",
        "normalize_agent_config",
    ),
    "normalize_mcp_tools": (
        "omnicoreagent.core.runtime.config",
        "normalize_mcp_tools",
    ),
    "normalize_model_config": (
        "omnicoreagent.core.runtime.config",
        "normalize_model_config",
    ),
}


def __getattr__(name: str) -> Any:
    return runtime(name)


def runtime(name: str) -> Any:
    if name in globals():
        return globals()[name]
    if name not in _RUNTIME_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = _RUNTIME_EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def runtime_logger() -> Any:
    return runtime("logger")


class LazyDefaultPromptBuilder:
    def __init__(self):
        self._builder = None

    def _load(self):
        if self._builder is None:
            self._builder = runtime("OmniCoreAgentPromptBuilder")(
                runtime("REACT_AGENT_PROMPT")
            )
        return self._builder

    def build(self, *, system_instruction: str) -> str:
        return self._load().build(system_instruction=system_instruction)


def build_model_config(model_config: Any) -> dict[str, Any]:
    return runtime("normalize_model_config")(model_config)


def build_mcp_tools(mcp_tools: list[Any] | None) -> list[dict[str, Any]]:
    return runtime("normalize_mcp_tools")(mcp_tools)


def build_agent_config(name: str, agent_config: Any = None) -> dict[str, Any]:
    return runtime("normalize_agent_config")(name, agent_config)


def normalize_local_tools(local_tools: Any) -> Any:
    if not isinstance(local_tools, list):
        return local_tools

    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    registry = ToolRegistry()
    for tool in local_tools:
        registry.register(tool)
    return registry


def default_memory_router() -> Any:
    return runtime("MemoryRouter")(memory_store_type="in_memory")


def default_event_router() -> Any:
    return runtime("EventRouter")(event_store_type="in_memory")


def build_guardrail(agent_name: str, agent_config: dict[str, Any]) -> tuple[str, Any]:
    guardrail_mode = agent_config.get("guardrail_mode", "full")
    if guardrail_mode == "off":
        runtime_logger().info(f"Guardrail disabled for agent '{agent_name}'")
        return guardrail_mode, None

    guardrail_config = agent_config.get("guardrail_config", {})
    detection_config = runtime("DetectionConfig")(**guardrail_config)
    guardrail = runtime("PromptInjectionGuard")(detection_config)
    runtime_logger().info(
        f"Guardrail enabled for agent '{agent_name}' (mode: {guardrail_mode})"
    )
    return guardrail_mode, guardrail


def create_llm_runtime(
    *,
    mcp_tools: list[dict[str, Any]],
    model_config: dict[str, Any],
    debug: bool,
) -> tuple[Any, Any]:
    if not mcp_tools:
        llm_connection = runtime("LLMConnection")(
            model_config=model_config,
            api_key=model_config.get("api_key"),
        )
        return None, llm_connection

    from omnicoreagent.mcp_clients_connection.client import MCPClient

    mcp_client = MCPClient(
        servers=mcp_tools,
        model_config=model_config,
        api_key=model_config.get("api_key"),
        debug=debug,
    )
    return mcp_client, mcp_client.llm_connection


def build_agent_settings(agent_config: dict[str, Any]) -> Any:
    return runtime("AgentConfig")(**agent_config)


def configure_memory_router(
    *,
    memory_router: Any,
    agent_settings: Any,
    summarize_fn: Any,
):
    if not memory_router:
        return

    summary_config = agent_settings.memory_config.get("summary")
    memory_router.set_memory_config(
        mode=agent_settings.memory_config["mode"],
        value=agent_settings.memory_config["value"],
        summary_config=summary_config,
        summarize_fn=summarize_fn
        if summary_config and summary_config.get("enabled")
        else None,
    )


def create_react_agent(
    *,
    agent_settings: Any,
    guardrail: Any,
    guardrail_mode: str,
) -> Any:
    tool_guardrail = guardrail if guardrail_mode == "full" else None
    return runtime("ReactAgent")(config=agent_settings, guardrail=tool_guardrail)


def prepare_dynamic_subagents(
    *,
    enabled: bool,
    existing_factory: Any,
    base_model_config: dict[str, Any],
    mcp_tools: list[dict[str, Any]],
    local_tools: Any,
    agent_config: dict[str, Any],
    prompt_builder: Any,
    event_router: Any,
    memory_router: Any,
    debug: bool,
) -> tuple[Any, Any]:
    if not enabled:
        return existing_factory, local_tools
    if existing_factory is not None:
        return existing_factory, local_tools

    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    if local_tools is None:
        local_tools = ToolRegistry()

    factory = runtime("SubagentFactory")(
        base_model_config=base_model_config,
        mcp_tools=mcp_tools,
        local_tools=local_tools,
        agent_config=agent_config,
        prompt_builder=prompt_builder,
        event_router=event_router,
        memory_router=memory_router,
        debug=debug,
    )
    runtime("build_subagent_tools")(factory, local_tools)
    return factory, local_tools


def index_tools_for_advanced_use(
    *,
    enabled: bool,
    mcp_tools: dict[str, Any] | None = None,
    local_tools: Any = None,
):
    if not enabled:
        return

    runtime("AdvanceToolsUse")().load_and_process_tools(
        mcp_tools=mcp_tools,
        local_tools=local_tools,
    )


def summary_instruction(max_tokens: int | None = None) -> str:
    instruction = runtime("FAST_CONVERSATION_SUMMARY_PROMPT")
    if max_tokens:
        instruction += f" Keep the summary roughly under {max_tokens} tokens."
    return instruction


def render_history(messages: list[dict[str, Any]]) -> str:
    return "".join(
        f"{message.get('role', 'unknown')}: {message.get('content', '')}\n"
        for message in messages
    )


def extract_summary_text(response: Any) -> str:
    if not response:
        return ""

    if hasattr(response, "choices") and response.choices:
        return response.choices[0].message.content.strip()
    if hasattr(response, "message"):
        return response.message.content.strip()
    if hasattr(response, "text"):
        return response.text.strip()
    if hasattr(response, "content"):
        return response.content.strip()
    if isinstance(response, dict) and "choices" in response:
        return response["choices"][0]["message"]["content"].strip()
    if isinstance(response, str):
        return response

    runtime_logger().error(
        f"No valid response content found in LLM response: {type(response)}"
    )
    return ""


def available_tools(mcp_client: Any, local_tools: Any) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []

    if mcp_client:
        for server_tools in mcp_client.available_tools.values():
            tools.extend(_available_mcp_tool(tool) for tool in server_tools)

    if local_tools:
        tools.extend(local_tools.get_available_tools())

    return tools


def _available_mcp_tool(tool: Any) -> dict[str, Any]:
    if isinstance(tool, dict):
        return {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "inputSchema": tool.get("inputSchema", {}),
            "type": "mcp",
        }

    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": tool.inputSchema,
        "type": "mcp",
    }
