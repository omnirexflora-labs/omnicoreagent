"""
Tests for OmniCoreAgent dynamic subagent harness support.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omnicoreagent import OmniCoreAgent
from omnicoreagent.core.subagents import SubagentFactory, build_subagent_tools
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.workspace.config import WorkspaceConfig
from omnicoreagent.core.workspace.tools import (
    build_tool_registry_workspace_files,
    validate_workspace_tool_name_conflicts,
)
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.runtime.config import normalize_agent_config


@pytest.fixture
def model_config():
    return {"provider": "openai", "model": "gpt-4o", "api_key": "test"}


@pytest.fixture
def factory(model_config):
    return SubagentFactory(base_model_config=model_config)


class SubagentPromptBuilder:
    def build_subagent_prompt(self, *, role: str, task: str, output_path: str) -> str:
        return f"ROLE={role}\nTASK={task}\nOUTPUT={output_path}"


class TestSubagentFactory:
    def test_subagent_config_caps_steps_and_preserves_harness_settings(
        self, model_config
    ):
        factory = SubagentFactory(
            base_model_config=model_config,
            agent_config={
                "max_steps": 50,
                "enable_subagents": True,
                "enable_workspace_files": False,
                "context_management": {"enabled": True},
                "tool_offload": {"enabled": True},
            },
        )

        config = factory._build_subagent_config()

        assert config["max_steps"] == 15
        assert config["enable_subagents"] is False
        assert config["enable_workspace_files"] is True
        assert config["context_management"] == {"enabled": True}
        assert config["tool_offload"] == {"enabled": True}

    def test_subagent_config_preserves_lower_step_limit(self, model_config):
        factory = SubagentFactory(
            base_model_config=model_config,
            agent_config={"max_steps": 7},
        )

        assert factory._build_subagent_config()["max_steps"] == 7

    def test_create_subagent(self, factory):
        agent = factory.create_subagent(
            name="test",
            role="Test role",
            task="Test task",
            output_path="/workspace/test/output.md",
        )

        assert agent.name == "subagent_test"
        assert "Test role" in agent.system_instruction
        assert "Test task" in agent.system_instruction
        assert agent.agent_config["enable_workspace_files"] is True
        assert agent.agent_config["enable_subagents"] is False

    def test_create_subagent_uses_custom_prompt_builder(self, model_config):
        factory = SubagentFactory(
            base_model_config=model_config,
            prompt_builder=SubagentPromptBuilder(),
        )

        agent = factory.create_subagent(
            name="custom",
            role="Researcher",
            task="Research X",
            output_path="/workspace/x/output.md",
        )

        assert agent.system_instruction == (
            "ROLE=Researcher\nTASK=Research X\nOUTPUT=/workspace/x/output.md"
        )

    def test_subagent_local_tools_none_when_parent_has_none(self, factory):
        assert factory._build_subagent_local_tools() is None

    def test_subagent_local_tools_preserves_non_registry_integrations(
        self, model_config
    ):
        local_tools = object()
        factory = SubagentFactory(
            base_model_config=model_config,
            local_tools=local_tools,
        )

        assert factory._build_subagent_local_tools() is local_tools

    def test_subagent_local_tools_preserves_internal_workspace_provider(
        self, model_config, tmp_path
    ):
        local_tools = ToolRegistry()
        build_tool_registry_workspace_files(
            registry=local_tools,
            workspace_config=WorkspaceConfig(workspace_dir=tmp_path / "workspace"),
        )
        factory = SubagentFactory(
            base_model_config=model_config,
            local_tools=local_tools,
        )

        child_tools = factory._build_subagent_local_tools()

        assert child_tools.get_tool_provider("read_file") == "workspace"
        validate_workspace_tool_name_conflicts(child_tools)

    @pytest.mark.asyncio
    async def test_run_subagent(self, factory):
        with patch.object(OmniCoreAgent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"response": "Output saved"}

            result = await factory.run_subagent(
                name="researcher",
                role="Research expert",
                task="Research topic X",
                output_path="/workspace/tasks/test/output.md",
            )

            assert result["status"] == "success"
            assert result["data"]["subagent_name"] == "researcher"

    @pytest.mark.asyncio
    async def test_run_subagent_connects_mcp_before_run(self, model_config):
        factory = SubagentFactory(
            base_model_config=model_config,
            mcp_tools=[{"name": "tools", "transport_type": "stdio", "command": "npx"}],
        )

        with (
            patch.object(
                OmniCoreAgent,
                "connect_mcp_servers",
                new_callable=AsyncMock,
            ) as mock_connect,
            patch.object(OmniCoreAgent, "run", new_callable=AsyncMock) as mock_run,
        ):
            mock_run.return_value = {"response": "Output saved in memory"}

            result = await factory.run_subagent(
                name="researcher",
                role="Research expert",
                task="Research topic X",
                output_path="/workspace/tasks/test/output.md",
            )

        assert result["status"] == "success"
        mock_connect.assert_awaited_once()
        mock_run.assert_awaited_once_with("Research topic X")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "response",
        [
            "",
            "short",
            "The model encountered an error while running.",
            "Unable to complete the task.",
            "Failed to write the output.",
        ],
    )
    async def test_run_subagent_detects_error_responses(self, factory, response):
        with patch.object(OmniCoreAgent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"response": response}

            result = await factory.run_subagent(
                name="researcher",
                role="Research expert",
                task="Research topic X",
                output_path="/workspace/tasks/test/output.md",
            )

        assert result["status"] == "error"
        assert result["data"]["subagent_name"] == "researcher"

    @pytest.mark.asyncio
    async def test_run_subagent_handles_non_string_response(self, factory):
        with patch.object(OmniCoreAgent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {
                "response": {"saved": True, "path": "/workspace/x"}
            }

            result = await factory.run_subagent(
                name="researcher",
                role="Research expert",
                task="Research topic X",
                output_path="/workspace/tasks/test/output.md",
            )

        assert result["status"] == "success"
        assert "saved" in result["data"]["summary"]

    @pytest.mark.asyncio
    async def test_run_subagent_handles_exceptions_and_cleans_active_agent(
        self, factory
    ):
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=RuntimeError("boom"))
        agent.cleanup = AsyncMock()
        factory.create_subagent = MagicMock(return_value=agent)
        factory._active_subagents["researcher"] = agent

        result = await factory.run_subagent(
            name="researcher",
            role="Research expert",
            task="Research topic X",
            output_path="/workspace/tasks/test/output.md",
        )

        assert result["status"] == "error"
        assert result["data"]["error"] == "boom"
        agent.cleanup.assert_awaited_once()
        assert "researcher" not in factory._active_subagents

    @pytest.mark.asyncio
    async def test_run_parallel_empty_list(self, factory):
        result = await factory.run_parallel_subagents([])

        assert result["status"] == "success"
        assert result["data"]["results"] == []

    @pytest.mark.asyncio
    async def test_run_parallel_subagents_success(self, factory):
        factory.run_subagent = AsyncMock(
            side_effect=[
                {
                    "status": "success",
                    "data": {"subagent_name": "a", "summary": "A done"},
                },
                {
                    "status": "success",
                    "data": {"subagent_name": "b", "summary": "B done"},
                },
            ]
        )

        result = await factory.run_parallel_subagents(
            [
                {"name": "a", "role": "A", "task": "Do A", "output_path": "/a.md"},
                {"name": "b", "role": "B", "task": "Do B", "output_path": "/b.md"},
            ]
        )

        assert result["status"] == "success"
        assert result["data"]["total"] == 2
        assert result["data"]["successful"] == 2
        assert result["data"]["failed"] == 0

    @pytest.mark.asyncio
    async def test_run_parallel_subagents_partial_failure(self, factory):
        factory.run_subagent = AsyncMock(
            side_effect=[
                {
                    "status": "success",
                    "data": {"subagent_name": "a", "summary": "A done"},
                },
                {
                    "status": "error",
                    "data": {"subagent_name": "b", "error": "bad task"},
                },
            ]
        )

        result = await factory.run_parallel_subagents(
            [
                {"name": "a", "role": "A", "task": "Do A", "output_path": "/a.md"},
                {"name": "b", "role": "B", "task": "Do B", "output_path": "/b.md"},
            ]
        )

        assert result["status"] == "partial"
        assert result["data"]["successful"] == 1
        assert result["data"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_run_parallel_subagents_all_failed(self, factory):
        factory.run_subagent = AsyncMock(
            return_value={
                "status": "error",
                "data": {"subagent_name": "a", "error": "bad task"},
            }
        )

        result = await factory.run_parallel_subagents(
            [{"name": "a", "role": "A", "task": "Do A", "output_path": "/a.md"}]
        )

        assert result["status"] == "error"
        assert result["data"]["successful"] == 0
        assert result["data"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_run_parallel_subagents_handles_raised_exceptions(self, factory):
        factory.run_subagent = AsyncMock(side_effect=RuntimeError("worker crashed"))

        result = await factory.run_parallel_subagents(
            [{"name": "a", "role": "A", "task": "Do A", "output_path": "/a.md"}]
        )

        assert result["status"] == "error"
        assert result["data"]["results"][0]["status"] == "error"
        assert result["data"]["results"][0]["error"] == "worker crashed"

    @pytest.mark.asyncio
    async def test_run_parallel_subagents_applies_defaults_for_sparse_specs(
        self, factory
    ):
        factory.run_subagent = AsyncMock(
            return_value={
                "status": "success",
                "data": {"subagent_name": "subagent_0", "summary": "done"},
            }
        )

        await factory.run_parallel_subagents([{}])

        factory.run_subagent.assert_awaited_once_with(
            name="subagent_0",
            role="Assistant",
            task="",
            output_path="/workspace/tasks/default/subagent_0/",
        )

    @pytest.mark.asyncio
    async def test_cleanup(self, factory):
        factory._active_subagents["test"] = MagicMock()
        factory._active_subagents["test"].cleanup = AsyncMock()

        await factory.cleanup()

        assert len(factory._active_subagents) == 0

    def test_build_subagent_tools_registers_single_spawn_tool(self, factory):
        registry = ToolRegistry()

        build_subagent_tools(factory, registry)

        tool_names = [tool.name for tool in registry.list_tools()]
        assert tool_names == ["spawn_subagents"]

    @pytest.mark.asyncio
    async def test_tool_wrapper_handles_list_input(self, factory):
        registry = ToolRegistry()
        build_subagent_tools(factory, registry)
        spawn_tool = registry.get_tool("spawn_subagents")
        factory.run_parallel_subagents = AsyncMock(return_value={"status": "success"})

        input_list = [{"name": "test", "role": "r", "task": "t", "output_path": "p"}]
        await spawn_tool.execute({"subagents_json": input_list})

        factory.run_parallel_subagents.assert_called_once_with(input_list)

    @pytest.mark.asyncio
    async def test_tool_wrapper_handles_json_string_input(self, factory):
        registry = ToolRegistry()
        build_subagent_tools(factory, registry)
        spawn_tool = registry.get_tool("spawn_subagents")
        factory.run_parallel_subagents = AsyncMock(return_value={"status": "success"})

        await spawn_tool.execute(
            {
                "subagents_json": (
                    '[{"name": "test", "role": "r", "task": "t", "output_path": "p"}]'
                )
            }
        )

        factory.run_parallel_subagents.assert_called_once_with(
            [{"name": "test", "role": "r", "task": "t", "output_path": "p"}]
        )

    @pytest.mark.asyncio
    async def test_tool_wrapper_rejects_invalid_json(self, factory):
        registry = ToolRegistry()
        build_subagent_tools(factory, registry)
        spawn_tool = registry.get_tool("spawn_subagents")

        result = await spawn_tool.execute({"subagents_json": "{not json"})

        assert result["status"] == "error"
        assert "Invalid JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_tool_wrapper_rejects_non_array_json(self, factory):
        registry = ToolRegistry()
        build_subagent_tools(factory, registry)
        spawn_tool = registry.get_tool("spawn_subagents")

        result = await spawn_tool.execute({"subagents_json": '{"name": "single"}'})

        assert result["status"] == "error"
        assert result["message"] == "subagents_json must be a JSON array"

    @pytest.mark.asyncio
    async def test_spawn_tool_schema_requires_array_parameter(self, factory):
        registry = ToolRegistry()
        build_subagent_tools(factory, registry)
        spawn_tool = registry.get_tool("spawn_subagents")

        assert spawn_tool.inputSchema["required"] == ["subagents_json"]
        assert spawn_tool.inputSchema["additionalProperties"] is False

    def test_created_subagent_does_not_inherit_spawn_tool(self, model_config):
        registry = ToolRegistry()

        @registry.register_tool("user_tool")
        def user_tool(value: str) -> str:
            return value

        factory = SubagentFactory(
            base_model_config=model_config,
            local_tools=registry,
            agent_config={
                "enable_subagents": True,
                "enable_workspace_files": False,
            },
        )
        build_subagent_tools(factory, registry)

        agent = factory.create_subagent(
            name="worker",
            role="Focused worker",
            task="Do focused work",
            output_path="/workspace/task/worker.md",
        )

        tool_names = [tool.name for tool in agent.local_tools.list_tools()]
        assert "user_tool" in tool_names
        assert "spawn_subagents" not in tool_names
        assert agent.agent_config["enable_subagents"] is False
        assert agent.agent_config["enable_workspace_files"] is True


class TestOmniCoreAgentSubagents:
    @pytest.mark.asyncio
    async def test_enable_subagents_disabled_does_not_register_spawn_tool(
        self, model_config
    ):
        agent = OmniCoreAgent(
            name="CoreHarness",
            system_instruction="Test",
            model_config=model_config,
            agent_config={"enable_subagents": False, "guardrail_mode": "off"},
        )

        await agent.initialize()

        assert agent._subagent_factory is None
        assert agent.local_tools is None

        await agent.cleanup()

    def test_enable_subagents_forces_workspace_files(self):
        config = normalize_agent_config(
            "Harness",
            {
                "enable_subagents": True,
                "enable_workspace_files": False,
            },
        )

        assert config["enable_subagents"] is True
        assert config["enable_workspace_files"] is True

    @pytest.mark.asyncio
    async def test_enable_subagents_registers_core_spawn_tool(self, model_config):
        agent = OmniCoreAgent(
            name="CoreHarness",
            system_instruction="Test",
            model_config=model_config,
            agent_config={"enable_subagents": True, "guardrail_mode": "off"},
        )

        assert agent.agent_config["enable_workspace_files"] is True

        await agent.initialize()

        tool_names = [tool.name for tool in agent.local_tools.list_tools()]
        assert tool_names.count("spawn_subagents") == 1

        await agent.cleanup()

    @pytest.mark.asyncio
    async def test_initialize_dynamic_subagents_is_idempotent(self, model_config):
        agent = OmniCoreAgent(
            name="CoreHarness",
            system_instruction="Test",
            model_config=model_config,
            agent_config={"enable_subagents": True, "guardrail_mode": "off"},
        )

        await agent.initialize()
        first_factory = agent._subagent_factory
        await agent.initialize()

        assert agent._subagent_factory is first_factory
        tool_names = [tool.name for tool in agent.local_tools.list_tools()]
        assert tool_names.count("spawn_subagents") == 1

        await agent.cleanup()

    @pytest.mark.asyncio
    async def test_enable_subagents_preserves_user_tools(self, model_config):
        user_registry = ToolRegistry()

        @user_registry.register_tool("my_custom_tool")
        def my_custom_tool(data: str) -> str:
            return data

        agent = OmniCoreAgent(
            name="ToolsTest",
            system_instruction="Test",
            model_config=model_config,
            local_tools=user_registry,
            agent_config={"enable_subagents": True, "guardrail_mode": "off"},
        )

        await agent.initialize()

        tool_names = [tool.name for tool in agent.local_tools.list_tools()]
        assert "my_custom_tool" in tool_names
        assert "spawn_subagents" in tool_names

        await agent.cleanup()

    @pytest.mark.asyncio
    async def test_initialize_registers_spawn_and_workspace_files(self):
        agent = OmniCoreAgent(
            name="Harness",
            system_instruction="Test",
            model_config={"provider": "ollama", "model": "llama3"},
            agent_config={"enable_subagents": True},
        )

        await agent.initialize()
        runtime_tools = await agent.agent.tool_runtime_registry.prepare_tools(
            agent.local_tools
        )

        tool_names = [tool.name for tool in runtime_tools.list_tools()]
        assert "spawn_subagents" in tool_names
        assert "write_file" in tool_names
        assert "read_file" in tool_names

        await agent.cleanup()

    @pytest.mark.asyncio
    async def test_initialized_runtime_prompt_includes_dynamic_subagent_surface(self):
        agent = OmniCoreAgent(
            name="Harness",
            system_instruction="Test",
            model_config={"provider": "ollama", "model": "llama3"},
            agent_config={"enable_subagents": True, "guardrail_mode": "off"},
        )

        await agent.initialize()
        runtime_tools = await agent.agent.tool_runtime_registry.prepare_tools(
            agent.local_tools
        )
        session_state = agent.agent.session_state_store.reset_for_run(
            session_id="prompt-session",
            debug=False,
        )

        async def message_history(agent_name, session_id):
            return []

        await agent.agent.prepare_initial_messages(
            session_state=session_state,
            system_prompt="base system",
            session_id="prompt-session",
            message_history=message_history,
            mcp_tools={},
            local_tools=runtime_tools,
            sub_agents=None,
        )

        prompt = session_state.messages[0].content
        assert '<extension name="subagents_harness">' in prompt
        assert "<dynamic_spawn>" in prompt
        assert "<subagents_json>" in prompt
        assert '<extension name="workspace_files">' in prompt
        assert "spawn_subagents:" in prompt
        assert "read_file:" in prompt
        assert "write_file:" in prompt

        await agent.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_releases_subagent_factory(self, model_config):
        agent = OmniCoreAgent(
            name="Harness",
            system_instruction="Test",
            model_config=model_config,
            agent_config={"enable_subagents": True, "guardrail_mode": "off"},
        )
        await agent.initialize()
        agent._subagent_factory.cleanup = AsyncMock()

        await agent.cleanup()

        assert agent._subagent_factory is None

    @pytest.mark.asyncio
    async def test_run_uses_core_harness_without_legacy_wrapper(self):
        agent = OmniCoreAgent(
            name="Harness",
            system_instruction="Test",
            model_config={"provider": "ollama", "model": "llama3"},
            agent_config={
                "enable_subagents": True,
                "context_management": {"enabled": True},
                "tool_offload": {"enabled": True},
            },
        )
        await agent.initialize()
        agent.agent.run = AsyncMock(
            return_value={
                "answer": "done",
                "usage": Usage(
                    requests=1,
                    request_tokens=1,
                    response_tokens=1,
                    total_tokens=2,
                    total_time=0.1,
                ),
            }
        )

        result = await agent.run("Coordinate this task", session_id="session-1")

        assert result["response"] == "done"
        assert result["session_id"] == "session-1"
        assert agent.agent.run.await_args.kwargs["local_tools"] is agent.local_tools
        assert agent.agent.run.await_args.kwargs["sub_agents"] is None

        await agent.cleanup()

    def test_package_no_longer_exports_legacy_harness_alias(self):
        import omnicoreagent

        removed_export = "Deep" + "Agent"

        assert not hasattr(omnicoreagent, removed_export)
        assert removed_export not in omnicoreagent.__all__
