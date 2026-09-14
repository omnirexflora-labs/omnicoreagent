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


def build_skill_tools(*, skill_manager: Any, registry: ToolRegistry):
    from omnicoreagent.core.skills.tools import build_skill_tools as build_tools

    return build_tools(skill_manager=skill_manager, registry=registry)


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

    def _workspace_for_runtime_tools(self) -> Workspace:
        if self.workspace is None:
            from omnicoreagent.core.workspace.manager import Workspace

            self.workspace = Workspace.from_config(self.workspace_config)
        self.tool_offloader.bind_workspace(self.workspace)
        return self.workspace

    async def prepare_tools(self, local_tools: Any = None):
        registry = local_tools
        needs_internal_registry = (
            self.enable_advanced_tool_use
            or self.enable_subagents
            or self.enable_workspace_files
            or self.tool_offloader.config.enabled
            or (self.enable_agent_skills and self.skill_manager)
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
            )

        return registry
