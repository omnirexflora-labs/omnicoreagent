from __future__ import annotations

from typing import TYPE_CHECKING, Any

from omnicoreagent.core.workspace.artifacts import ToolResponseOffloader
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.workspace.config import WorkspaceConfig
from omnicoreagent.core.privacy import PrivacyFilter

if TYPE_CHECKING:
    from omnicoreagent.core.workspace.manager import Workspace


def build_tool_registry_workspace_files(
    *,
    registry: ToolRegistry,
    workspace_files_backend: Any = None,
    workspace: Workspace | None = None,
    workspace_config: WorkspaceConfig | dict | None = None,
    privacy_filter: PrivacyFilter | None = None,
):
    from omnicoreagent.core.workspace.tools import (
        build_tool_registry_workspace_files as build_workspace_files_tool,
    )

    return build_workspace_files_tool(
        registry=registry,
        workspace_files_backend=workspace_files_backend,
        workspace=workspace,
        workspace_config=workspace_config,
        privacy_filter=privacy_filter,
    )


def validate_workspace_tool_name_conflicts(registry: ToolRegistry):
    from omnicoreagent.core.workspace.tools import (
        validate_workspace_tool_name_conflicts as validate_conflicts,
    )

    validate_conflicts(registry)


def build_tool_registry_artifact_tool(
    *, offloader: ToolResponseOffloader, registry: ToolRegistry
):
    from omnicoreagent.core.workspace.artifact_tools import (
        build_tool_registry_artifact_tool as build_artifact_tools,
    )

    return build_artifact_tools(offloader=offloader, registry=registry)


def build_skill_tools(
    *, skill_manager: Any, registry: ToolRegistry, env_passthrough: list[str] | None = None
):
    from omnicoreagent.core.skills.tools import build_skill_tools as build_tools

    return build_tools(
        skill_manager=skill_manager, registry=registry, env_passthrough=env_passthrough
    )


class ToolRuntimeRegistry:
    """Prepare executable runtime tools and render tool schemas for prompts."""

    def __init__(
        self,
        register_internal_tool: ToolRegistry,
        tool_offloader: ToolResponseOffloader,
        enable_advanced_tool_use: bool = False,
        enable_subagents: bool = False,
        enable_workspace_files: bool = False,
        enable_agent_skills: bool = False,
        skill_manager: Any = None,
        workspace: Workspace | None = None,
        workspace_config: WorkspaceConfig | dict | None = None,
        privacy_filter: PrivacyFilter | None = None,
        sandbox_execution: Any = None,
        tool_call_timeout: int = 60,
        skill_script_env: list[str] | None = None,
        code_mode: Any = None,
    ):
        self.register_internal_tool = register_internal_tool
        self.tool_offloader = tool_offloader
        self.enable_advanced_tool_use = enable_advanced_tool_use
        self.enable_subagents = enable_subagents
        self.enable_workspace_files = enable_workspace_files or enable_subagents
        self.enable_agent_skills = enable_agent_skills
        self.skill_manager = skill_manager
        self.workspace = workspace
        self.workspace_config = workspace_config
        self.privacy_filter = privacy_filter
        self.sandbox_execution = sandbox_execution
        self.tool_call_timeout = tool_call_timeout
        self.skill_script_env = list(skill_script_env or [])
        self.code_mode = code_mode

    def _workspace_for_runtime_tools(self) -> Workspace:
        if self.workspace is None:
            from omnicoreagent.core.workspace.manager import Workspace

            self.workspace = Workspace.from_config(self.workspace_config)
        self.tool_offloader.bind_workspace(self.workspace)
        return self.workspace

    async def prepare_tools(self, local_tools: Any = None):
        # This agent's tools (files, artifacts, skills, execute, run_code) are
        # bound to its own workspace: they go on its own copy of your
        # registry, never on the registry itself, which other agents may
        # share (a child's run rebound a lead's file tools to the child's
        # workspace).
        registry = local_tools.copy() if callable(getattr(local_tools, "copy", None)) else local_tools
        needs_internal_registry = (
            self.enable_advanced_tool_use
            or self.enable_subagents
            or self.enable_workspace_files
            or self.tool_offloader.config.enabled
            or (self.enable_agent_skills and self.skill_manager)
            or self.sandbox_execution is not None
            or bool(getattr(self.code_mode, "enabled", False))
        )

        if registry is None and needs_internal_registry:
            registry = self.register_internal_tool

        if registry is None:
            return None

        if self.enable_workspace_files:
            validate_workspace_tool_name_conflicts(registry)
            build_tool_registry_workspace_files(
                registry=registry,
                workspace_files_backend=None,
                workspace=self._workspace_for_runtime_tools(),
                workspace_config=self.workspace_config,
                privacy_filter=self.privacy_filter,
            )

        if self.tool_offloader.config.enabled:
            build_tool_registry_artifact_tool(
                offloader=self.tool_offloader,
                registry=registry,
            )

        if self.enable_agent_skills and self.skill_manager:
            build_skill_tools(
                skill_manager=self.skill_manager,
                registry=registry,
                env_passthrough=self.skill_script_env,
            )

        if self.sandbox_execution is not None:
            from omnicoreagent.core.tools.execution_tools import build_execution_tools

            runtime = getattr(self.sandbox_execution.governance_engine, "sandbox_runtime", None)
            build_execution_tools(
                registry,
                max_timeout_seconds=self.tool_call_timeout,
                on_host=getattr(runtime, "execution_surface", "sandbox") == "host",
            )

        if getattr(self.code_mode, "enabled", False):
            from omnicoreagent.core.tools.code_mode import (
                build_code_mode_tool,
                callable_name,
                function_signature,
            )

            # Registered last, so its description lists every tool a program may call.
            signatures = [
                function_signature(tool["name"], tool.get("inputSchema") or {}, tool.get("description"))
                for tool in registry.get_available_tools()
                if self.code_mode.allows(tool["name"]) and callable_name(tool["name"])
            ]
            build_code_mode_tool(registry, config=self.code_mode, functions=signatures)

        return registry
