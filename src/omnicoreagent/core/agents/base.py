from __future__ import annotations

import time

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from omnicoreagent.core.system_prompts import AgentPromptContextBuilder
from omnicoreagent.core.token_usage import (
    Usage,
    UsageLimits,
)
from omnicoreagent.core.types import (
    AgentState,
    Message,
    ParsedResponse,
    SessionState,
    ToolCallResult,
    ToolError,
)
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.tool_batch_runner import ToolBatchRunner
from omnicoreagent.core.tools.tool_call_resolver import ToolCallResolver
from omnicoreagent.core.tools.tool_failure_handler import ToolFailureHandler
from omnicoreagent.core.tools.tool_runtime_registry import ToolRuntimeRegistry
from omnicoreagent.core.utils import (
    logger,
    BackgroundTaskManager,
)
from omnicoreagent.core.events.base import Event
from omnicoreagent.core.context_manager import (
    AgentLoopContextManager,
    ContextManagementConfig,
)
from omnicoreagent.core.tool_response_offloader import (
    ToolResponseOffloader,
    OffloadConfig,
)
from omnicoreagent.core.agents.initial_messages import AgentInitialMessagePreparer
from omnicoreagent.core.agents.llm_step import AgentLlmStepRunner
from omnicoreagent.core.agents.message_history import AgentMessageHistoryLoader
from omnicoreagent.core.agents import events as agent_events
from omnicoreagent.core.agents.run_outcome import AgentRunOutcomeHandler
from omnicoreagent.core.agents.session_state import AgentSessionStateStore
from omnicoreagent.core.agents.subagent_runner import SubAgentCallRunner
from omnicoreagent.core.agents.tool_action import AgentToolActionRunner
from omnicoreagent.core.tools.tool_observation import ToolObservationHandler
from omnicoreagent.core.agents.xml_parser import (
    parse_action_or_answer,
)

if TYPE_CHECKING:
    from omnicoreagent.core.guardrails import PromptInjectionGuard

class BaseReactAgent:
    """Autonomous agent implementing the ReAct paradigm for task solving through iterative reasoning and tool usage."""

    def __init__(
        self,
        agent_name: str,
        max_steps: int,
        tool_call_timeout: int,
        request_limit: int = 0,
        total_tokens_limit: int = 0,
        enable_advanced_tool_use: bool = False,
        enable_subagents: bool = False,
        enable_workspace_memory: bool = False,
        enable_agent_skills: bool = False,
        context_management_config: dict = None,
        tool_offload_config: dict = None,
        guardrail: PromptInjectionGuard | None = None,
    ):
        self.agent_name = agent_name
        self.max_steps = max(max_steps, 5)
        if max_steps < 5:
            logger.warning(
                f"Agent {agent_name}: max_steps increased from {max_steps} to 5 (minimum required for tool usage)"
            )
        self.tool_call_timeout = tool_call_timeout

        self.request_limit = request_limit
        self.total_tokens_limit = total_tokens_limit
        self._limits_enabled = request_limit > 0 or total_tokens_limit > 0
        self.enable_advanced_tool_use = enable_advanced_tool_use
        self.enable_subagents = enable_subagents

        self.enable_workspace_memory = enable_workspace_memory
        self.enable_agent_skills = enable_agent_skills
        self.skill_manager = None
        self.usage_limits = UsageLimits(
            request_limit=self.request_limit, total_tokens_limit=self.total_tokens_limit
        )

        self.session_state_store = AgentSessionStateStore(agent_name=self.agent_name)
        self._session_states = self.session_state_store.states
        self.background_task_manager = BackgroundTaskManager()
        self.init_skills()
        self.register_internal_tool = ToolRegistry()

        self.context_manager = AgentLoopContextManager(
            ContextManagementConfig.from_dict(context_management_config or {})
        )
        self.llm_step_runner = AgentLlmStepRunner(
            agent_name=self.agent_name,
            context_manager=self.context_manager,
            usage_limits=self.usage_limits,
            limits_enabled=self._limits_enabled,
            request_limit=self.request_limit,
        )

        self.tool_offloader = ToolResponseOffloader(
            config=OffloadConfig.from_dict(tool_offload_config or {})
        )
        self.guardrail = guardrail
        self.tool_observation_handler = ToolObservationHandler(
            agent_name=self.agent_name,
            tool_offloader=self.tool_offloader,
            guardrail=self.guardrail,
        )
        self.tool_call_resolver = ToolCallResolver(guardrail=self.guardrail)
        self.tool_failure_handler = ToolFailureHandler(agent_name=self.agent_name)
        self.message_history_loader = AgentMessageHistoryLoader(
            agent_name=self.agent_name
        )
        self.subagent_runner = SubAgentCallRunner(agent_name=self.agent_name)
        self.tool_batch_runner = ToolBatchRunner(
            agent_name=self.agent_name,
            tool_call_timeout=self.tool_call_timeout,
        )
        self.tool_action_runner = AgentToolActionRunner(
            agent_name=self.agent_name,
            tool_call_resolver=self.tool_call_resolver,
            tool_failure_handler=self.tool_failure_handler,
            tool_batch_runner=self.tool_batch_runner,
            tool_observation_handler=self.tool_observation_handler,
        )
        self.tool_runtime_registry = ToolRuntimeRegistry(
            register_internal_tool=self.register_internal_tool,
            tool_offloader=self.tool_offloader,
            enable_advanced_tool_use=self.enable_advanced_tool_use,
            enable_subagents=self.enable_subagents,
            enable_workspace_memory=self.enable_workspace_memory,
            enable_agent_skills=self.enable_agent_skills,
            skill_manager=self.skill_manager,
        )
        self.prompt_context_builder = AgentPromptContextBuilder(
            enable_advanced_tool_use=self.enable_advanced_tool_use,
            enable_subagents=self.enable_subagents,
            enable_workspace_memory=self.enable_workspace_memory,
            enable_agent_skills=self.enable_agent_skills,
            is_tool_offload_enabled=lambda: self.tool_offloader.config.enabled,
            skill_manager=self.skill_manager,
        )
        self.initial_message_preparer = AgentInitialMessagePreparer(
            tool_runtime_registry=self.tool_runtime_registry,
            message_history_loader=self.message_history_loader,
            prompt_context_builder=self.prompt_context_builder,
        )
        self.run_outcome_handler = AgentRunOutcomeHandler(agent_name=self.agent_name)

    def init_skills(self):
        if self.enable_agent_skills:
            from omnicoreagent.core.skills.manager import SkillManager

            self.skill_manager = SkillManager()
            self.skill_manager.discover_skills()
            logger.info(
                f"Agent Skills enabled: found {len(self.skill_manager.skills)} skills"
            )

    def _get_session_state(self, session_id: str, debug: bool) -> SessionState:
        return self.session_state_store.get(session_id=session_id, debug=debug)

    async def extract_action_or_answer(
        self,
        response: str,
        session_id: str,
        event_router: Callable,
        debug: bool = False,
    ) -> ParsedResponse:
        """Parse LLM response to extract a final answer, tool call, or agent call using XML format only."""
        await agent_events.emit_agent_thought_from_response(
            event_router=event_router,
            session_id=session_id,
            agent_name=self.agent_name,
            response=response,
        )
        return parse_action_or_answer(response, debug=debug)

    async def resolve_tool_call_request(
        self,
        parsed_response: ParsedResponse,
        sessions: dict,
        mcp_tools: dict,
        local_tools: Any = None,
        sub_agents: list = None,
    ) -> ToolError | list[ToolCallResult]:
        return await self.tool_call_resolver.resolve(
            parsed_response=parsed_response,
            sessions=sessions,
            mcp_tools=mcp_tools,
            local_tools=local_tools,
            sub_agents=sub_agents,
        )

    async def act(
        self,
        parsed_response: ParsedResponse,
        response: str,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        system_prompt: str,
        debug: bool = False,
        sessions: dict = None,
        mcp_tools: dict = None,
        local_tools: Any = None,
        session_id: str = None,
        event_router: Callable[[str, Event], Any] = None,
        sub_agents: list = None,
    ):
        await self.tool_action_runner.run(
            parsed_response=parsed_response,
            response=response,
            session_state=self._get_session_state(session_id=session_id, debug=debug),
            add_message_to_history=add_message_to_history,
            system_prompt=system_prompt,
            reset_system_prompt=self.reset_system_prompt,
            debug=debug,
            sessions=sessions,
            mcp_tools=mcp_tools,
            local_tools=local_tools,
            session_id=session_id,
            event_router=event_router,
            sub_agents=sub_agents,
        )

    async def reset_system_prompt(self, messages: list, system_prompt: str):
        old_messages = messages[1:]
        messages = [Message(role="system", content=system_prompt)]
        messages.extend(old_messages)
        return messages

    def agent_session_state_context(
        self, new_state: AgentState, session_id: str, debug: bool
    ):
        """Context manager to change the agent session state"""
        return self.session_state_store.state_context(
            new_state=new_state,
            session_id=session_id,
            debug=debug,
        )

    async def prepare_initial_messages(
        self,
        session_state,
        system_prompt: str,
        session_id: str,
        message_history: Callable[[], Any],
        mcp_tools: dict = None,
        local_tools: Any = None,
        debug: bool = False,
        sub_agents: list = None,
    ) -> None:
        await self.initial_message_preparer.prepare(
            session_state=session_state,
            system_prompt=system_prompt,
            session_id=session_id,
            message_history=message_history,
            mcp_tools=mcp_tools,
            local_tools=local_tools,
            sub_agents=sub_agents,
        )

    async def execute_sub_agent_calls(
        self,
        response: str,
        agent_calls: list,
        sub_agents: list,
        session_id: str,
        session_state: Any,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        run_usage: Usage,
        event_router: Callable[[str, Event], Any] = None,
        debug: bool = False,
    ):
        """
        Execute multiple sub-agent calls in parallel with proper observation formatting.

        This function:
        1. Connects all MCP servers concurrently (if needed)
        2. Executes all sub-agent runs concurrently
        3. Formats results into proper XML observations
        4. Adds observations to message history
        """
        await self.subagent_runner.execute(
            response=response,
            agent_calls=agent_calls,
            sub_agents=sub_agents,
            session_id=session_id,
            session_state=session_state,
            add_message_to_history=add_message_to_history,
            run_usage=run_usage,
            event_router=event_router,
            debug=debug,
        )

    async def run(
        self,
        system_prompt: str,
        query: str,
        llm_connection: Callable,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        message_history: Callable[[], Any],
        debug: bool = False,
        sessions: dict = None,
        mcp_tools: dict = None,
        local_tools: Any = None,
        session_id: str = None,
        event_router: Callable[[str, Event], Any] = None,
        sub_agents: list = None,
    ) -> Any:
        """Execute ReAct loop with JSON communication
        kwargs: if mcp is enbale then it will be sessions and availables_tools else it will be local_tools
        """
        session_state = self.session_state_store.reset_for_run(
            session_id=session_id, debug=debug
        )
        start_time = time.perf_counter()
        run_usage = Usage()

        await agent_events.emit_user_message(
            event_router=event_router,
            session_id=session_id,
            agent_name=self.agent_name,
            message=query,
        )

        await add_message_to_history(
            role="user",
            content=query,
            session_id=session_id,
            metadata={"agent_name": self.agent_name},
        )
        runtime_local_tools = await self.tool_runtime_registry.prepare_tools(
            local_tools=local_tools
        )
        await self.prepare_initial_messages(
            system_prompt=system_prompt,
            session_state=session_state,
            message_history=message_history,
            mcp_tools=mcp_tools,
            local_tools=runtime_local_tools,
            session_id=session_id,
            debug=debug,
            sub_agents=sub_agents,
        )
        if session_state.state not in [
            AgentState.IDLE,
            AgentState.ERROR,
        ]:
            raise RuntimeError(
                f"Agent is not in a valid state to run: {session_state.state}"
            )

        async with self.agent_session_state_context(
            new_state=AgentState.RUNNING, session_id=session_id, debug=debug
        ):
            current_steps = 0
            last_valid_response = None
            while (
                session_state.state not in [AgentState.FINISHED]
                and current_steps < self.max_steps
            ):
                current_steps += 1
                llm_step = await self.llm_step_runner.run(
                    session_state=session_state,
                    llm_connection=llm_connection,
                    run_usage=run_usage,
                    session_id=session_id,
                    event_router=event_router,
                    debug=debug,
                )
                if llm_step.error_result is not None:
                    return llm_step.error_result
                response = llm_step.response

                parsed_response = await self.extract_action_or_answer(
                    response=response,
                    debug=debug,
                    session_id=session_id,
                    event_router=event_router,
                )
                if debug:
                    logger.info(f"current steps: {current_steps}")
                if parsed_response.answer is not None:
                    last_valid_response = parsed_response.answer
                    return await self.run_outcome_handler.handle_final_answer(
                        answer=parsed_response.answer,
                        session_state=session_state,
                        add_message_to_history=add_message_to_history,
                        session_id=session_id,
                        event_router=event_router,
                        run_usage=run_usage,
                        start_time=start_time,
                    )

                if parsed_response.action is not None:
                    if parsed_response.agent_calls is not None:
                        agent_calls = parsed_response.data

                        await self.execute_sub_agent_calls(
                            response=response,
                            agent_calls=agent_calls,
                            sub_agents=sub_agents,
                            session_id=session_id,
                            session_state=session_state,
                            add_message_to_history=add_message_to_history,
                            run_usage=run_usage,
                            event_router=event_router,
                            debug=debug,
                        )
                    else:
                        await self.act(
                            parsed_response=parsed_response,
                            response=response,
                            add_message_to_history=add_message_to_history,
                            system_prompt=system_prompt,
                            mcp_tools=mcp_tools,
                            debug=debug,
                            sessions=sessions,
                            local_tools=runtime_local_tools,
                            session_id=session_id,
                            event_router=event_router,
                            sub_agents=sub_agents,
                        )

                if parsed_response.error is not None:
                    session_state.messages.append(
                        Message(
                            role="user",
                            content=parsed_response.error,
                        )
                    )
                    continue
                if current_steps >= self.max_steps:
                    session_state.state = AgentState.STUCK
                    return self.run_outcome_handler.max_steps_result(
                        max_steps=self.max_steps,
                        last_valid_response=last_valid_response,
                        run_usage=run_usage,
                    )

        if session_state.state == AgentState.STUCK and last_valid_response:
            return self.run_outcome_handler.loop_stuck_result(
                last_valid_response=last_valid_response
            )

        run_usage.total_time = time.perf_counter() - start_time
        return {"answer": last_valid_response, "usage": run_usage}
