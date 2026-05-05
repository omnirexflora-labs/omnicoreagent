"""
Test Suite for DeepAgent.

DeepAgent = OmniCoreAgent + Multi-Agent Orchestration
Works for any domain based on user's system_instruction and tools.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from omnicoreagent.deep_agent import DeepAgent
from omnicoreagent.deep_agent.prompts import (
    DeepAgentPromptBuilder,
)
from omnicoreagent.core.subagents import (
    SubagentFactory,
    build_subagent_tools,
)
from omnicoreagent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def model_config():
    return {"provider": "openai", "model": "gpt-4o"}


@pytest.fixture
def deep_agent(model_config):
    return DeepAgent(
        name="TestAgent",
        system_instruction="You are a test agent.",
        model_config=model_config,
    )


@pytest.fixture
def factory(model_config):
    return SubagentFactory(base_model_config=model_config)


@pytest.fixture
def prompt_builder():
    return DeepAgentPromptBuilder()


# =============================================================================
# Prompt Builder Tests
# =============================================================================


class TestDeepAgentPromptBuilder:
    def test_build_includes_user_instruction(self, prompt_builder):
        """User instruction should be in the prompt."""
        result = prompt_builder.build(user_instruction="You are a data analyst.")
        assert "You are a data analyst." in result
        assert "<system_instruction>" in result
        assert "</system_instruction>" in result

    def test_build_keeps_orchestration_out_of_deep_agent_prompt(self, prompt_builder):
        """Dynamic subagent orchestration is injected by OmniCoreAgent core."""
        result = prompt_builder.build(user_instruction="Base")
        assert "<dynamic_subagents_extension>" not in result
        assert "spawn_subagents" not in result

    def test_build_no_task_id_in_base_prompt(self, prompt_builder):
        """Base prompt should NOT have task_id - it's dynamic when spawning."""
        result = prompt_builder.build(user_instruction="Test")
        # task_id should not be in base prompt anymore
        assert "<task_id>" not in result or "task_name" in result  # Only example paths

    def test_build_includes_react_runtime_prompt(self, prompt_builder):
        """ReAct runtime prompt should be included."""
        result = prompt_builder.build(user_instruction="Test")
        assert "<react_pattern>" in result
        assert "<tool_usage>" in result

    def test_build_subagent_prompt(self, prompt_builder):
        """Subagent prompt should be focused."""
        result = prompt_builder.build_subagent_prompt(
            role="AWS specialist",
            task="Research AWS pricing",
            output_path="/memories/test/",
        )
        assert "AWS specialist" in result
        assert "Research AWS pricing" in result
        assert "/memories/test/" in result

# =============================================================================
# SubagentFactory Tests
# =============================================================================


class TestSubagentFactory:
    @pytest.mark.asyncio
    async def test_create_subagent(self, factory):
        """Test subagent creation."""
        agent = factory.create_subagent(
            name="test",
            role="Test role",
            task="Test task",
            output_path="/memories/test/",
        )

        assert agent.name == "subagent_test"
        assert "Test role" in agent.system_instruction
        assert "Test task" in agent.system_instruction

    @pytest.mark.asyncio
    async def test_run_subagent(self, factory):
        """Test running a subagent."""
        with patch.object(OmniCoreAgent, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"response": "Findings saved"}

            result = await factory.run_subagent(
                name="researcher",
                role="Research expert",
                task="Research topic X",
                output_path="/memories/tasks/test/",
            )

            # New standard tool return format
            assert result["status"] == "success"
            assert result["data"]["subagent_name"] == "researcher"

    @pytest.mark.asyncio
    async def test_run_parallel_empty_list(self, factory):
        """Test parallel with empty list."""
        result = await factory.run_parallel_subagents([])
        # New standard tool return format
        assert result["status"] == "success"
        assert result["data"]["results"] == []

    @pytest.mark.asyncio
    async def test_cleanup(self, factory):
        """Test cleanup clears tracked subagents."""
        factory._active_subagents["test"] = MagicMock()
        factory._active_subagents["test"].cleanup = AsyncMock()

        await factory.cleanup()
        assert len(factory._active_subagents) == 0

    @pytest.mark.asyncio
    async def test_build_subagent_tools(self, factory):
        """Test tools are registered."""
        registry = ToolRegistry()
        build_subagent_tools(factory, registry)

        tool_names = [t.name for t in registry.list_tools()]
        assert "spawn_subagents" in tool_names

    @pytest.mark.asyncio
    async def test_tool_wrapper_handles_list_input(self, factory):
        """Test that the registered tool wrapper handles list input."""
        registry = ToolRegistry()
        build_subagent_tools(factory, registry)

        spawn_tool = registry.get_tool("spawn_subagents")

        # Mock factory.run_parallel_subagents to avoid actual execution
        factory.run_parallel_subagents = AsyncMock(return_value={"status": "success"})

        # Pass a LIST directly, simulating framework auto-parsing
        input_list = [{"name": "test", "role": "r", "task": "t", "output_path": "p"}]
        await spawn_tool.execute({"subagents_json": input_list})

        # Verify it called the factory method with the list
        factory.run_parallel_subagents.assert_called_once_with(input_list)

    def test_created_subagent_does_not_inherit_spawn_tool(self, model_config):
        """Spawned workers should inherit user tools without recursive delegation."""
        registry = ToolRegistry()

        @registry.register_tool("user_tool")
        def user_tool(value: str) -> str:
            return value

        factory = SubagentFactory(
            base_model_config=model_config,
            local_tools=registry,
            agent_config={
                "enable_subagents": True,
                "enable_workspace_memory": False,
            },
        )
        build_subagent_tools(factory, registry)

        agent = factory.create_subagent(
            name="worker",
            role="Focused worker",
            task="Do focused work",
            output_path="/memories/task/worker.md",
        )

        tool_names = [tool.name for tool in agent.local_tools.list_tools()]
        assert "user_tool" in tool_names
        assert "spawn_subagents" not in tool_names
        assert agent.agent_config["enable_subagents"] is False
        assert agent.agent_config["enable_workspace_memory"] is True


# =============================================================================
# DeepAgent Initialization Tests
# =============================================================================


class TestDeepAgentInitialization:
    @pytest.mark.asyncio
    async def test_basic_initialization(self, deep_agent):
        """Test DeepAgent initializes correctly."""
        await deep_agent.initialize()

        assert deep_agent._initialized is True
        assert deep_agent._agent is not None
        assert deep_agent._agent._subagent_factory is not None

        await deep_agent.cleanup()

    @pytest.mark.asyncio
    async def test_deep_agent_enables_workspace_memory(self):
        """DeepAgent should enable workspace memory tools."""
        agent = DeepAgent(
            name="Test",
            system_instruction="Test",
            model_config={"provider": "openai", "model": "gpt-4"},
            agent_config={},
        )
        assert agent.agent_config["enable_workspace_memory"] is True

    def test_omnicoreagent_enable_subagents_registers_core_spawn_tool(self, model_config):
        """OmniCoreAgent owns dynamic subagent spawning when enabled."""
        agent = OmniCoreAgent(
            name="CoreHarness",
            system_instruction="Test",
            model_config=model_config,
            agent_config={"enable_subagents": True},
        )

        assert agent.agent_config["enable_workspace_memory"] is True

        agent._prepare_dynamic_subagents()

        tool_names = [tool.name for tool in agent.local_tools.list_tools()]
        assert tool_names.count("spawn_subagents") == 1

    @pytest.mark.asyncio
    async def test_prompt_builder_assigned(self, deep_agent):
        """Prompt builder should be DeepAgentPromptBuilder."""
        assert isinstance(deep_agent.prompt_builder, DeepAgentPromptBuilder)

    @pytest.mark.asyncio
    async def test_subagent_tools_registered(self, deep_agent):
        """Subagent spawning tools should be available."""
        await deep_agent.initialize()

        tool_names = [t.name for t in deep_agent._agent.local_tools.list_tools()]
        assert "spawn_subagents" in tool_names

        await deep_agent.cleanup()


# =============================================================================
# DeepAgent Run Tests
# =============================================================================


class TestDeepAgentRun:
    @pytest.mark.asyncio
    async def test_run_delegates_to_agent(self, deep_agent):
        """Run should delegate to underlying agent."""
        await deep_agent.initialize()

        mock_response = {"response": "Task complete", "metric": {}}
        deep_agent._agent.run = AsyncMock(return_value=mock_response)

        result = await deep_agent.run("Do something")

        assert result == mock_response
        await deep_agent.cleanup()

    @pytest.mark.asyncio
    async def test_run_auto_initializes(self, deep_agent):
        """Run should auto-initialize if needed."""
        assert deep_agent._initialized is False

        with patch.object(
            deep_agent, "initialize", new_callable=AsyncMock
        ) as mock_init:

            async def side_effect():
                deep_agent._initialized = True
                deep_agent._agent = MagicMock()
                deep_agent._agent.run = AsyncMock(return_value={"response": "OK"})

            mock_init.side_effect = side_effect
            await deep_agent.run("Test")
            mock_init.assert_called_once()


# =============================================================================
# Lifecycle Tests
# =============================================================================


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_cleanup_releases_resources(self, deep_agent):
        """Cleanup should release all resources."""
        await deep_agent.initialize()
        await deep_agent.cleanup()
        assert deep_agent._initialized is False

    @pytest.mark.asyncio
    async def test_cleanup_before_init_safe(self, deep_agent):
        """Cleanup before init shouldn't fail."""
        await deep_agent.cleanup()
        assert deep_agent._initialized is False


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_preserves_user_tools(self):
        """User tools should be preserved alongside spawn tools."""
        user_registry = ToolRegistry()

        @user_registry.register_tool("my_custom_tool")
        def my_custom_tool(data: str) -> str:
            return data

        agent = DeepAgent(
            name="ToolsTest",
            system_instruction="Test",
            model_config={"provider": "openai", "model": "gpt-4"},
            local_tools=user_registry,
        )
        await agent.initialize()

        tool_names = [t.name for t in agent._agent.local_tools.list_tools()]
        assert "my_custom_tool" in tool_names
        assert "spawn_subagents" in tool_names

        await agent.cleanup()

    @pytest.mark.asyncio
    async def test_multiple_agents_independent(self):
        """Multiple agents should be independent."""
        agent1 = DeepAgent(
            name="Agent1",
            system_instruction="Test 1",
            model_config={"provider": "openai", "model": "gpt-4"},
        )
        agent2 = DeepAgent(
            name="Agent2",
            system_instruction="Test 2",
            model_config={"provider": "openai", "model": "gpt-4"},
        )

        await agent1.initialize()
        await agent2.initialize()

        # They should be independent
        assert agent1.name != agent2.name
        assert agent1._agent is not agent2._agent

        await agent1.cleanup()
        await agent2.cleanup()
