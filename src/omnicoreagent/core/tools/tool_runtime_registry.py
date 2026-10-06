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
    allows: Any = None,
    effect: Any = None,
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
        allows=allows,
        effect=effect,
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
        governance_engine: Any = None,
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
        self.governance_engine = governance_engine
        # The only tools this agent is offered, by name, or None for all: a
        # worker's profile narrows what its lead has (core/worker_profiles.py).
        self.only_tools: set[str] | None = None

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
                allows=self._allows if self.governance_engine is not None else None,
                effect=self._effect if self.governance_engine is not None else None,
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

        if self.only_tools is not None:
            for name in list(registry.tools):
                if name not in self.only_tools:
                    registry.tools.pop(name)
                    registry._internal_tool_providers.pop(name, None)

        if getattr(self.code_mode, "enabled", False) and (
            self.only_tools is None or "run_code" in self.only_tools
        ):
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

    def _effect(self, tool_name: str, tool_args: dict) -> str:
        """What the policy would decide for this workspace call: "allow", "ask"
        or "deny". A folder operation is refused for a file under a deny rule,
        and for one under an ask rule unless a person approved the operation
        as a whole (0.5.1, B5). A policy that cannot say is a deny."""
        from omnicoreagent.governance.capabilities import tool_authority_requests
        from omnicoreagent.governance.models import PolicyEffect

        engine = self.governance_engine
        try:
            effects = {
                engine.evaluator.evaluate(engine.policy, request).effect
                for request in tool_authority_requests(
                    tool_name=tool_name, tool_args=tool_args, tool_provider="workspace"
                )
            }
        except Exception:
            return "deny"
        if PolicyEffect.DENY in effects or not effects <= {PolicyEffect.ALLOW, PolicyEffect.ASK}:
            return "deny"
        return "ask" if PolicyEffect.ASK in effects else "allow"

    def _allows(self, tool_name: str, tool_args: dict) -> bool:
        """Whether the policy would allow this workspace call without asking.
        grep, glob and ls ask it for each file (read_file): they were checked
        on the folder searched only, and grep returned a file a read rule
        protected (the 0.5.0rc6 gate). Deleting or moving a folder asks it for
        each file under it: `prod` does not match a rule on `prod/*` (the rc7
        security review)."""
        from omnicoreagent.governance.capabilities import tool_authority_requests
        from omnicoreagent.governance.models import PolicyEffect

        engine = self.governance_engine
        try:
            requests = tool_authority_requests(
                tool_name=tool_name, tool_args=tool_args, tool_provider="workspace"
            )
            return all(
                engine.evaluator.evaluate(engine.policy, request).effect == PolicyEffect.ALLOW
                for request in requests
            )
        except Exception:
            return False  # a policy that cannot say is treated as refusing
