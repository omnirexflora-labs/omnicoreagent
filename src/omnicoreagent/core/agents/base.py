import asyncio
import time

import json
import re
import uuid
from collections.abc import Callable
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, Tuple, List, Dict
from omnicoreagent.core.system_prompts import (
    tools_retriever_additional_prompt,
    memory_tool_additional_prompt,
    sub_agents_additional_prompt,
    agent_skills_additional_prompt,
)
import inspect
from omnicoreagent.core.token_usage import (
    Usage,
    UsageLimitExceeded,
    UsageLimits,
    session_stats,
    usage,
)
from omnicoreagent.core.tools.tools_handler import (
    LocalToolHandler,
    MCPToolHandler,
    ToolExecutor,
)
from omnicoreagent.core.types import (
    AgentState,
    Message,
    ParsedResponse,
    ToolCall,
    ToolCallMetadata,
    ToolCallResult,
    ToolError,
    ToolFunction,
    SessionState,
)
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.utils import (
    RobustLoopDetector,
    handle_stuck_state,
    logger,
    show_tool_response,
    track,
    normalize_tool_args,
    build_xml_observations_block,
    BackgroundTaskManager,
    resolve_agent,
    build_kwargs,
    build_sub_agents_observation_xml,
    show_sub_agent_call_result,
)
from datetime import datetime
from omnicoreagent.core.events.base import (
    Event,
    EventType,
    ToolCallErrorPayload,
    ToolCallStartedPayload,
    ToolCallResultPayload,
    FinalAnswerPayload,
    AgentMessagePayload,
    UserMessagePayload,
    AgentThoughtPayload,
    SubAgentCallStartedPayload,
    SubAgentCallResultPayload,
    SubAgentCallErrorPayload,
)
from omnicoreagent.core.tools.advance_tools_use import (
    build_tool_registry_advance_tools_use,
)
from omnicoreagent.core.tools.memory_tool.memory_tool import (
    build_tool_registry_memory_tool,
)
from omnicoreagent.core.skills.tools import build_skill_tools


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
        memory_tool_backend: str = None,
        enable_agent_skills: bool = False,
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

        self.memory_tool_backend = memory_tool_backend
        self.enable_agent_skills = enable_agent_skills
        self.skill_manager = None
        self.usage_limits = UsageLimits(
            request_limit=self.request_limit, total_tokens_limit=self.total_tokens_limit
        )

        self._session_states: dict[Tuple[str, str], SessionState] = {}
        self.background_task_manager = BackgroundTaskManager()
        self.init_skills()
        self.register_internal_tool = ToolRegistry()

    def init_skills(self):
        if self.enable_agent_skills:
            from omnicoreagent.core.skills.manager import SkillManager

            self.skill_manager = SkillManager()
            self.skill_manager.discover_skills()
            logger.info(
                f"Agent Skills enabled: found {len(self.skill_manager.skills)} skills"
            )

    def _get_session_state(self, session_id: str, debug: bool) -> SessionState:
        key = (session_id, self.agent_name)
        if key not in self._session_states:
            self._session_states[key] = SessionState(
                messages=[],
                state=AgentState.IDLE,
                loop_detector=RobustLoopDetector(debug=debug),
                assistant_with_tool_calls=None,
                pending_tool_responses=[],
            )
        return self._session_states[key]

    async def extract_action_or_answer(
        self,
        response: str,
        session_id: str,
        event_router: Callable,
        debug: bool = False,
    ) -> ParsedResponse:
        """Parse LLM response to extract a final answer, tool call, or agent call using XML format only."""
        try:
            agent_thoughts = re.search(r"<thought>(.*?)</thought>", response, re.DOTALL)
            if agent_thoughts:
                event = Event(
                    type=EventType.AGENT_THOUGHT,
                    payload=AgentThoughtPayload(
                        message=str(agent_thoughts.group(1).strip()),
                    ),
                    agent_name=self.agent_name,
                )
                if event_router:
                    self.background_task_manager.run_background_strict(
                        event_router(session_id=session_id, event=event)
                    )

            tool_calls = []
            tool_call_blocks = []

            if "<tool_calls>" in response and "</tool_calls>" in response:
                if debug:
                    logger.info("Multiple tool calls detected.")
                block_match = re.search(
                    r"<tool_calls>(.*?)</tool_calls>", response, re.DOTALL
                )
                if block_match:
                    tool_call_blocks = re.findall(
                        r"<tool_call>(.*?)</tool_call>", block_match.group(1), re.DOTALL
                    )

            elif "<tool_call>" in response and "</tool_call>" in response:
                if debug:
                    logger.info("Single tool call detected.")
                single_match = re.search(
                    r"<tool_call>(.*?)</tool_call>", response, re.DOTALL
                )
                tool_call_blocks = [single_match.group(1)] if single_match else []

            for block in tool_call_blocks:
                name_match = re.search(
                    r"<tool_name>(.*?)</tool_name>", block, re.DOTALL
                ) or re.search(r"<name>(.*?)</name>", block, re.DOTALL)
                args_match = re.search(
                    r"<parameters>(.*?)</parameters>", block, re.DOTALL
                ) or re.search(r"<args>(.*?)</args>", block, re.DOTALL)
                if not (name_match and args_match):
                    return ParsedResponse(
                        error="Invalid tool call format - missing name or parameters"
                    )
                tool_name = name_match.group(1).strip()
                args_str = args_match.group(1).strip()
                args = {}
                if args_str.startswith("{") and args_str.endswith("}"):
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError as e:
                        return ParsedResponse(error=f"Invalid JSON in args: {str(e)}")
                else:
                    for key, value in re.findall(
                        r"<(\w+)>(.*?)</\1>", args_str, re.DOTALL
                    ):
                        value = value.strip()
                        if (value.startswith("[") and value.endswith("]")) or (
                            value.startswith("{") and value.endswith("}")
                        ):
                            try:
                                args[key] = json.loads(value)
                            except json.JSONDecodeError:
                                args[key] = value
                        else:
                            args[key] = value
                tool_calls.append({"tool": tool_name, "parameters": args})

            if tool_calls:
                return ParsedResponse(
                    action=True, data=json.dumps(tool_calls), tool_calls=True
                )

            agent_calls = []
            agent_call_blocks = []

            if "<agent_calls>" in response and "</agent_calls>" in response:
                if debug:
                    logger.info("Multiple agent calls detected.")
                block_match = re.search(
                    r"<agent_calls>(.*?)</agent_calls>", response, re.DOTALL
                )
                if block_match:
                    agent_call_blocks = re.findall(
                        r"<agent_call>(.*?)</agent_call>",
                        block_match.group(1),
                        re.DOTALL,
                    )

            elif "<agent_call>" in response and "</agent_call>" in response:
                if debug:
                    logger.info("Single agent call detected.")
                single_match = re.search(
                    r"<agent_call>(.*?)</agent_call>", response, re.DOTALL
                )
                agent_call_blocks = [single_match.group(1)] if single_match else []

            for block in agent_call_blocks:
                name_match = re.search(
                    r"<agent_name>(.*?)</agent_name>", block, re.DOTALL
                ) or re.search(r"<name>(.*?)</name>", block, re.DOTALL)
                args_match = re.search(
                    r"<parameters>(.*?)</parameters>", block, re.DOTALL
                ) or re.search(r"<args>(.*?)</args>", block, re.DOTALL)
                if not (name_match and args_match):
                    return ParsedResponse(
                        error="Invalid agent call format - missing name or parameters"
                    )
                agent_name = name_match.group(1).strip()
                args_str = args_match.group(1).strip()
                args = {}
                if args_str.startswith("{") and args_str.endswith("}"):
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError as e:
                        return ParsedResponse(error=f"Invalid JSON in args: {str(e)}")
                else:
                    for key, value in re.findall(
                        r"<(\w+)>(.*?)</\1>", args_str, re.DOTALL
                    ):
                        value = value.strip()
                        if (value.startswith("[") and value.endswith("]")) or (
                            value.startswith("{") and value.endswith("}")
                        ):
                            try:
                                args[key] = json.loads(value)
                            except json.JSONDecodeError:
                                args[key] = value
                        else:
                            args[key] = value
                agent_calls.append({"agent": agent_name, "parameters": args})

            if agent_calls:
                return ParsedResponse(
                    action=True, data=json.dumps(agent_calls), agent_calls=True
                )

            final_answer_match = re.search(
                r"<final_answer>(.*?)</final_answer>", response, re.DOTALL
            )
            if final_answer_match:
                return ParsedResponse(answer=final_answer_match.group(1).strip())

            if "<" in response and ">" in response:
                return ParsedResponse(
                    error="Response contains XML but not in required format. Use <thought>, <final_answer> or during tool calls use <tool_call> tag or <agent_call> tag during agent calls."
                )

            return ParsedResponse(
                error="Response must use XML format. Wrap in <thought> and <final_answer> or call  pigment tag or <agent_call> tag during agent calls. or <tool_calls> tag during tool calls."
            )

        except Exception as e:
            logger.error("Error parsing model response: %s", str(e))
            return ParsedResponse(error=str(e))

    @track("memory_processing")
    async def update_llm_working_memory(
        self,
        message_history: Callable[[], Any],
        session_id: str,
        llm_connection: Callable,
        debug: bool,
    ):
        """Update the LLM's working memory with the current message history and process memory asynchronously"""

        short_term_memory_message_history = await message_history(
            agent_name=self.agent_name, session_id=session_id
        )
        if not short_term_memory_message_history:
            return

        validated_messages = [
            Message.model_validate(msg) if isinstance(msg, dict) else msg
            for msg in short_term_memory_message_history
        ]
        session_state = self._get_session_state(session_id=session_id, debug=debug)
        for message in validated_messages:
            role = message.role
            metadata = message.metadata

            if role == "user":
                if not message.content.strip().startswith("<observations>"):
                    self._try_flush_pending(session_id=session_id, debug=debug)
                    session_state.messages.append(
                        Message(role="user", content=message.content)
                    )

            elif role == "assistant":
                if metadata.has_tool_calls:
                    self._try_flush_pending(session_id=session_id, debug=debug)
                    session_state.assistant_with_tool_calls = {
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": (
                            [tc.model_dump() for tc in metadata.tool_calls]
                            if metadata.tool_calls
                            else []
                        ),
                    }
                    session_state.pending_tool_responses = []
                else:
                    self._try_flush_pending(session_id=session_id, debug=debug)
                    session_state.messages.append(
                        Message(role="assistant", content=message.content)
                    )

            elif role == "tool":
                session_state.pending_tool_responses.append(
                    {
                        "role": "tool",
                        "content": message.content,
                        "tool_call_id": metadata.tool_call_id,
                    }
                )
                self._try_flush_pending(session_id=session_id, debug=debug)

            else:
                logger.warning(f"Unknown message role encountered: {role}")

    def _try_flush_pending(self, session_id: str, debug: bool):
        session_state = self._get_session_state(session_id=session_id, debug=debug)
        if session_state.assistant_with_tool_calls:
            expected = {
                tc["id"]
                for tc in session_state.assistant_with_tool_calls.get("tool_calls", [])
            }
            actual = {
                resp["tool_call_id"] for resp in session_state.pending_tool_responses
            }
            if not (expected - actual):
                session_state.messages.append(session_state.assistant_with_tool_calls)
                session_state.messages.extend(session_state.pending_tool_responses)
                session_state.assistant_with_tool_calls = None
                session_state.pending_tool_responses = []

    async def resolve_tool_call_request(
        self,
        parsed_response: ParsedResponse,
        sessions: dict,
        mcp_tools: dict,
        local_tools: Any = None,
        sub_agents: list = None,
    ) -> ToolError | list[ToolCallResult]:
        try:
            local_tools = await self.process_local_tools(
                local_tools=local_tools, local_tool_verification=True
            )
            actions = json.loads(parsed_response.data)
            if not isinstance(actions, list):
                actions = [actions]

            results: list[ToolCallResult] = []

            for action in actions:
                tool_name = action.get("tool", "").strip()
                if tool_name == "tools_retriever":
                    mcp_tools = None
                tool_args = action.get("parameters", {})
                if sub_agents:
                    sub_agent_names = [sub_agent.name for sub_agent in sub_agents]
                    if tool_name in sub_agent_names:
                        return ToolError(
                            observation=(
                                f"INVOCATION ERROR: '{tool_name}' is a sub-agent, not a tool.\n\n"
                                f"❌ You used (WRONG):\n"
                                f"   <tool_call><tool_name>{tool_name}</tool_name><parameters>...</parameters></tool_call>\n\n"
                                f"✓ Must use (CORRECT):\n"
                                f"   <agent_call><agent_name>{tool_name}</agent_name><parameters>...</parameters></agent_call>\n\n"
                                f"ACTION REQUIRED:\n"
                                f"1. Check AVAILABLE SUB AGENT REGISTRY for '{tool_name}' parameter requirements\n"
                                f"2. Retry using <agent_call> with <agent_name> tags\n"
                                f"3. Ensure parameters match registry definition exactly"
                            ),
                            tool_name="N/A",
                            tool_args=tool_args,
                        )

                if not tool_name:
                    return ToolError(
                        observation="No tool name provided in the request",
                        tool_name="N/A",
                        tool_args=tool_args,
                    )

                mcp_tool_found = False
                tool_executor = None
                tool_data = {}

                if mcp_tools:
                    for server_name, tools in mcp_tools.items():
                        for tool in tools:
                            if tool.name.lower() == tool_name.lower():
                                mcp_tool_handler = MCPToolHandler(
                                    sessions=sessions,
                                    tool_data=json.dumps(action),
                                    mcp_tools=mcp_tools,
                                )
                                tool_executor = ToolExecutor(
                                    tool_handler=mcp_tool_handler
                                )
                                tool_data = (
                                    await mcp_tool_handler.validate_tool_call_request(
                                        tool_data=json.dumps(action),
                                        mcp_tools=mcp_tools,
                                    )
                                )
                                mcp_tool_found = True
                                break
                        if mcp_tool_found:
                            break

                if not mcp_tool_found and local_tools:
                    local_tool_handler = LocalToolHandler(local_tools=local_tools)
                    tool_executor = ToolExecutor(tool_handler=local_tool_handler)
                    tool_data = await local_tool_handler.validate_tool_call_request(
                        tool_data=json.dumps(action),
                        local_tools=local_tools,
                    )

                if not mcp_tool_found and not local_tools:
                    return ToolError(
                        observation=f"The tool named '{tool_name}' does not exist in the available tools.",
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )

                if not tool_data.get("action"):
                    return ToolError(
                        observation=tool_data.get("error", "Tool validation failed"),
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )

                results.append(
                    ToolCallResult(
                        tool_executor=tool_executor,
                        tool_name=tool_data.get("tool_name"),
                        tool_args=normalize_tool_args(tool_data.get("tool_args")),
                    )
                )

            return results

        except Exception as e:
            logger.error(f"Error resolving tool call request: {e}")
            return ToolError(observation=str(e), tool_name="unknown", tool_args={})

    async def parse_tool_observation(self, raw_output: str) -> dict:
        """
        Normalizes and parses tool output into a **single, consistent structure**.

        Handles:
        - JSON string outputs
        - Aggregated multi-tool outputs
        - Old-style {successes:[], errors:[]} format
        - Non-JSON string errors

        Always returns:
        {
            "status": "success" | "partial" | "error",
            "tools_results": [
                {
                    "tool_name": str,
                    "args": dict | None,
                    "status": "success" | "error",
                    "data": dict | str | None,
                    "message": str | None,
                },
                ...
            ]
        }
        """
        try:
            if isinstance(raw_output, str):
                try:
                    parsed = json.loads(raw_output)
                except json.JSONDecodeError:
                    logger.warning(
                        "parse_tool_observation: raw_output is not valid JSON."
                    )
                    return {
                        "status": "error",
                        "tools_results": [
                            {
                                "tool_name": "unknown",
                                "args": None,
                                "status": "error",
                                "data": None,
                                "message": raw_output,
                            }
                        ],
                    }
            elif isinstance(raw_output, dict):
                parsed = raw_output
            else:
                return {
                    "status": "error",
                    "tools_results": [
                        {
                            "tool_name": "unknown",
                            "args": None,
                            "status": "error",
                            "data": None,
                            "message": str(raw_output),
                        }
                    ],
                }

            normalized_results = []

            if "tools_results" in parsed:
                raw_results = parsed["tools_results"]
            elif "successes" in parsed or "errors" in parsed:
                raw_results = []
                for s in parsed.get("successes", []):
                    raw_results.append({**s, "status": "success"})
                for e in parsed.get("errors", []):
                    raw_results.append({**e, "status": "error"})
            else:
                raw_results = [parsed]

            for item in raw_results:
                tool_name = item.get("tool_name") or item.get("tool") or "unknown"
                status = item.get("status", "success")
                args = item.get("args")

                data = item.get("data")
                message = item.get("message") or item.get("error")

                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except json.JSONDecodeError:
                        pass

                normalized_results.append(
                    {
                        "tool_name": tool_name,
                        "args": args,
                        "status": status,
                        "data": data,
                        "message": message,
                    }
                )

            success_count = sum(
                1 for r in normalized_results if r["status"] == "success"
            )
            error_count = sum(1 for r in normalized_results if r["status"] == "error")

            if success_count > 0 and error_count == 0:
                global_status = "success"
            elif success_count > 0 and error_count > 0:
                global_status = "partial"
            else:
                global_status = "error"

            return {
                "status": global_status,
                "tools_results": normalized_results,
            }

        except Exception as e:
            logger.error(f"Error parsing tool observation: {e}", exc_info=True)
            return {
                "status": "error",
                "tools_results": [
                    {
                        "tool_name": "unknown",
                        "args": None,
                        "status": "error",
                        "data": None,
                        "message": f"Observation parsing failed: {str(e)}",
                    }
                ],
            }

    @track("tool_execution")
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
        session_state = self._get_session_state(session_id=session_id, debug=debug)

        tool_call_result = await self.resolve_tool_call_request(
            parsed_response=parsed_response,
            mcp_tools=mcp_tools,
            sessions=sessions,
            local_tools=local_tools,
            sub_agents=sub_agents,
        )

        tools_results = []
        obs_text = None

        if isinstance(tool_call_result, ToolError):
            tool_errors = (
                tool_call_result.errors
                if hasattr(tool_call_result, "errors")
                else [tool_call_result]
            )
            obs_text = (
                tool_call_result.observation
                if hasattr(tool_call_result, "observation")
                else str(tool_call_result)
            )

            for single_tool in tool_errors:
                tool_name = getattr(single_tool, "tool_name", "unknown")
                tool_args = getattr(single_tool, "tool_args", {})
                error_message = getattr(single_tool, "observation", obs_text)

                event = Event(
                    type=EventType.TOOL_CALL_ERROR,
                    payload=ToolCallErrorPayload(
                        tool_name=tool_name,
                        error_message=error_message,
                    ),
                    agent_name=self.agent_name,
                )

                if event_router:
                    self.background_task_manager.run_background_strict(
                        event_router(session_id=session_id, event=event)
                    )
                session_state.loop_detector.record_tool_call(
                    str(tool_name),
                    str(tool_args),
                    str(error_message),
                )

            combined_tool_name = "_and_".join(
                [getattr(t, "tool_name", "unknown") for t in tool_errors]
            )
            combined_tool_args = [getattr(t, "tool_args", {}) for t in tool_errors]

            logger.error(
                f"Tool call validation failed for: {combined_tool_name} "
                f"args={combined_tool_args} -> {obs_text}"
            )

            for single_tool in tool_errors:
                tools_results.append(
                    {
                        "tool_name": getattr(single_tool, "tool_name", "unknown"),
                        "args": getattr(single_tool, "tool_args", {}),
                        "status": "error",
                        "data": None,
                        "message": getattr(single_tool, "observation", obs_text),
                    }
                )
        else:
            tool_call_id = str(uuid.uuid4())
            combined_tool_name = "_and_".join([t.tool_name for t in tool_call_result])
            combined_tool_args = [t.tool_args for t in tool_call_result]

            tool_calls_metadata = ToolCallMetadata(
                agent_name=self.agent_name,
                has_tool_calls=True,
                tool_call_id=tool_call_id,
                tool_calls=[
                    ToolCall(
                        id=tool_call_id,
                        function=ToolFunction(
                            name=combined_tool_name[:60],
                            arguments=json.dumps(combined_tool_args),
                        ),
                    )
                ],
            )

            event = Event(
                type=EventType.TOOL_CALL_STARTED,
                payload=ToolCallStartedPayload(
                    tool_name=combined_tool_name,
                    tool_args=json.dumps(combined_tool_args),
                    tool_call_id=tool_call_id,
                ),
                agent_name=self.agent_name,
            )
            if event_router:
                self.background_task_manager.run_background_strict(
                    event_router(session_id=session_id, event=event)
                )

            await add_message_to_history(
                role="assistant",
                content=response,
                metadata=tool_calls_metadata.model_dump(),
                session_id=session_id,
            )
            session_state.messages.append(Message(role="assistant", content=response))

            tools_results = []
            try:
                async with asyncio.timeout(self.tool_call_timeout):
                    first_executor = tool_call_result[0].tool_executor
                    tool_output = await first_executor.execute(
                        agent_name=self.agent_name,
                        tool_args=combined_tool_args,
                        tool_name=combined_tool_name,
                        tool_call_id=tool_call_id,
                        add_message_to_history=add_message_to_history,
                        session_id=session_id,
                    )

                observation = await self.parse_tool_observation(tool_output)

                tools_results = observation.get("tools_results", [])
                obs_lines = []
                success_count = 0
                error_count = 0

                if not isinstance(tool_call_result, (list, tuple)):
                    tool_call_result = [tool_call_result]

                tool_counter = defaultdict(int)
                seen_tools: set[str] = set()
                for single_tool, result in zip(tool_call_result, tools_results):
                    tool_name = result.get("tool_name", "unknown_tool")
                    args = result.get("args", {})
                    status = result.get("status", "unknown")
                    data = result.get("data")
                    message = result.get("message", "")
                    tool_counter[tool_name] += 1
                    tool_call_generated_id = f"{tool_name}#{tool_counter[tool_name]}"
                    display_value = data if data is not None else message
                    if tool_name not in seen_tools:
                        seen_tools.add(tool_name)
                        session_state.loop_detector.record_tool_call(
                            str(tool_name),
                            str(args),
                            str(display_value),
                        )

                    if status == "success":
                        obs_lines.append(f"{tool_call_generated_id}: {display_value}")
                        success_count += 1
                    elif status == "error":
                        reason = display_value or "Unknown error occurred."
                        obs_lines.append(f"{tool_call_generated_id} ERROR: {reason}")
                        error_count += 1
                    else:
                        obs_lines.append(
                            f"{tool_call_generated_id}: Unexpected status '{status}'"
                        )
                        error_count += 1
                seen_tools.clear()
                if success_count == len(tools_results):
                    status = "success"
                    obs_text = "\n\n".join(obs_lines)
                elif success_count > 0 and error_count > 0:
                    status = "partial"
                    obs_text = "Partial success:\n" + "\n\n".join(obs_lines)
                elif error_count == len(tools_results):
                    status = "error"
                    error_details = "\n\n".join(obs_lines)
                    obs_text = f"Tool execution failed completely:\n{error_details}"
                else:
                    status = observation.get("status", "unknown")
                    obs_text = "\n\n".join(obs_lines) or "No valid tool results."

                event = Event(
                    type=EventType.TOOL_CALL_RESULT,
                    payload=ToolCallResultPayload(
                        tool_name=combined_tool_name,
                        tool_args=json.dumps(combined_tool_args),
                        result=obs_text,
                        tool_call_id=tool_call_id,
                    ),
                    agent_name=self.agent_name,
                )
                if event_router:
                    self.background_task_manager.run_background_strict(
                        event_router(session_id=session_id, event=event)
                    )

            except asyncio.TimeoutError:
                obs_text = (
                    "Tool call timed out. Please try again or use a different approach."
                )
                logger.warning(obs_text)
                for single_tool in tool_call_result:
                    session_state.loop_detector.record_tool_call(
                        str(single_tool.tool_name),
                        str(single_tool.tool_args),
                        obs_text,
                    )

                for single_tool in tool_call_result:
                    tools_results.append(
                        {
                            "tool_name": getattr(single_tool, "tool_name", "unknown"),
                            "args": getattr(single_tool, "tool_args", {}),
                            "status": "error",
                            "data": None,
                            "message": obs_text,
                        }
                    )

                await add_message_to_history(
                    role="tool",
                    content=obs_text,
                    metadata={
                        "tool_call_id": tool_call_id,
                        "agent_name": self.agent_name,
                    },
                    session_id=session_id,
                )

                event = Event(
                    type=EventType.TOOL_CALL_ERROR,
                    payload=ToolCallErrorPayload(
                        tool_name=combined_tool_name,
                        error_message=obs_text,
                    ),
                    agent_name=self.agent_name,
                )
                if event_router:
                    self.background_task_manager.run_background_strict(
                        event_router(session_id=session_id, event=event)
                    )

            except Exception as e:
                obs_text = f"Error executing tool: {str(e)}"
                logger.error(obs_text)
                for single_tool in tool_call_result:
                    session_state.loop_detector.record_tool_call(
                        str(single_tool.tool_name),
                        str(single_tool.tool_args),
                        obs_text,
                    )

                for single_tool in tool_call_result:
                    tools_results.append(
                        {
                            "tool_name": getattr(single_tool, "tool_name", "unknown"),
                            "args": getattr(single_tool, "tool_args", {}),
                            "status": "error",
                            "data": None,
                            "message": obs_text,
                        }
                    )

                await add_message_to_history(
                    role="tool",
                    content=obs_text,
                    metadata={
                        "tool_call_id": tool_call_id,
                        "agent_name": self.agent_name,
                    },
                    session_id=session_id,
                )
                event = Event(
                    type=EventType.TOOL_CALL_ERROR,
                    payload=ToolCallErrorPayload(
                        tool_name=combined_tool_name,
                        error_message=obs_text,
                    ),
                    agent_name=self.agent_name,
                )
                if event_router:
                    self.background_task_manager.run_background_strict(
                        event_router(session_id=session_id, event=event)
                    )

        if debug:
            show_tool_response(
                agent_name=self.agent_name,
                tool_name=combined_tool_name,
                tool_args=combined_tool_args,
                observation=obs_text,
            )

        xml_obs_block = build_xml_observations_block(tools_results)
        session_state.messages.append(
            Message(
                role="user",
                content=xml_obs_block,
            )
        )
        await add_message_to_history(
            role="user",
            content=xml_obs_block,
            session_id=session_id,
            metadata={"agent_name": self.agent_name},
        )

        if debug:
            logger.info(
                f"Agent state changed from {session_state.state} to {AgentState.OBSERVING}"
            )
        session_state.state = AgentState.OBSERVING

        if isinstance(tool_call_result, (list, tuple)):
            tool_call_results = list(tool_call_result)
        else:
            tool_call_results = [tool_call_result]

        for single_tool in tool_call_results:
            tool_name = getattr(single_tool, "tool_name", None)
            if not tool_name:
                if isinstance(single_tool, (list, tuple)) and len(single_tool) >= 1:
                    tool_name = single_tool[0]
                else:
                    logger.warning(
                        "Skipping malformed tool_call_result item: %s", single_tool
                    )
                    continue

            if session_state.loop_detector.is_looping(tool_name):
                loop_type = session_state.loop_detector.get_loop_type(tool_name)
                logger.warning(
                    f"Tool call loop detected for '{tool_name}': {loop_type}"
                )

                new_system_prompt = handle_stuck_state(system_prompt)
                session_state.messages = await self.reset_system_prompt(
                    messages=session_state.messages,
                    system_prompt=new_system_prompt,
                )

                loop_message = (
                    f"Observation:\n"
                    f"⚠️ Tool call loop detected for '{tool_name}': {loop_type}\n\n"
                    "Current approach is not working. You MUST now provide a final answer to the user.\n"
                    "Please:\n"
                    "1. Stop trying the same approach\n"
                    "2. Provide your best response to the user based on what you know\n"
                    "3. Use <final_answer>Your response here</final_answer> format\n"
                    "4. Be helpful and explain any limitations if needed\n"
                    "5. Do NOT continue with more tool calls\n"
                    "\nYou MUST respond with <final_answer> tags now.\n"
                )

                event = Event(
                    type=EventType.TOOL_CALL_ERROR,
                    payload=ToolCallErrorPayload(
                        tool_name=tool_name,
                        error_message=loop_message,
                    ),
                    agent_name=self.agent_name,
                )
                if event_router:
                    self.background_task_manager.run_background_strict(
                        event_router(session_id=session_id, event=event)
                    )

                session_state.messages.append(
                    Message(role="user", content=loop_message)
                )

                if debug:
                    logger.info(
                        f"Agent state changed from {session_state.state} to {AgentState.STUCK}"
                    )

                session_state.state = AgentState.STUCK
                session_state.loop_detector.reset(tool_name)

    async def reset_system_prompt(self, messages: list, system_prompt: str):
        old_messages = messages[1:]
        messages = [Message(role="system", content=system_prompt)]
        messages.extend(old_messages)
        return messages

    @asynccontextmanager
    async def agent_session_state_context(
        self, new_state: AgentState, session_id: str, debug: bool
    ):
        """Context manager to change the agent session state"""
        session_state = self._get_session_state(session_id=session_id, debug=debug)
        if not isinstance(new_state, AgentState):
            raise ValueError(f"Invalid agent state: {new_state}")
        previous_state = session_state.state
        session_state.state = new_state
        try:
            yield
        except Exception as e:
            session_state.state = AgentState.ERROR
            logger.error(f"Error in agent state context: {e}")
            raise
        finally:
            session_state.state = previous_state

    async def process_local_tools(
        self, local_tools: Any = None, local_tool_verification: bool = False
    ):
        if self.enable_advanced_tool_use:
            if not local_tool_verification:
                local_tools = self.register_internal_tool
                await build_tool_registry_advance_tools_use(
                    registry=local_tools,
                )
            else:
                if local_tools and local_tool_verification:
                    await build_tool_registry_advance_tools_use(
                        registry=local_tools,
                    )

        if self.memory_tool_backend:
            if local_tools:
                build_tool_registry_memory_tool(
                    memory_tool_backend=self.memory_tool_backend,
                    registry=local_tools,
                )
            else:
                local_tools = self.register_internal_tool
                build_tool_registry_memory_tool(
                    memory_tool_backend=self.memory_tool_backend,
                    registry=local_tools,
                )
        if self.enable_agent_skills and self.skill_manager:
            if local_tools:
                build_skill_tools(
                    skill_manager=self.skill_manager,
                    registry=local_tools,
                )
            else:
                local_tools = self.register_internal_tool
                build_skill_tools(
                    skill_manager=self.skill_manager,
                    registry=local_tools,
                )
        return local_tools

    async def get_tools_registry(
        self, mcp_tools: dict = None, local_tools: Any = None
    ) -> str:
        lines = ["Available tools:"]

        def format_param_type(param_info: dict) -> str:
            """Format parameter type with nested structure details."""
            p_type = param_info.get("type", "any")

            if p_type == "array":
                items = param_info.get("items", {})
                if items:
                    item_type = items.get("type", "any")
                    if item_type == "object":
                        props = items.get("properties", {})
                        if props:
                            fields = ", ".join(
                                [
                                    f'"{k}": {v.get("type", "any")}'
                                    for k, v in props.items()
                                ]
                            )
                            return f"array of objects ({{{fields}}})"
                        return "array of objects"
                    else:
                        return f"array of {item_type}s"
                return "array"

            elif p_type == "object":
                props = param_info.get("properties", {})
                if props:
                    fields = ", ".join(
                        [f'"{k}": {v.get("type", "any")}' for k, v in props.items()]
                    )
                    return f"object ({{{fields}}})"
                return "object"

            return p_type

        def format_param_description(param_info: dict) -> str:
            """Format parameter description with structure examples."""
            p_desc = param_info.get("description", "").replace("\n", " ").strip()
            p_type = param_info.get("type", "any")

            if p_type == "array":
                items = param_info.get("items", {})
                if items.get("type") == "object":
                    props = items.get("properties", {})
                    if props:
                        example_fields = []
                        for k, v in props.items():
                            v_type = v.get("type", "any")
                            if v_type == "string":
                                example_fields.append(f'"{k}": "..."')
                            elif v_type == "number":
                                example_fields.append(f'"{k}": 0')
                            elif v_type == "boolean":
                                example_fields.append(f'"{k}": true')
                            else:
                                example_fields.append(f'"{k}": ...')

                        example = "{" + ", ".join(example_fields) + "}"
                        if p_desc:
                            p_desc += f". Example: {example}"
                        else:
                            p_desc = f"Example: {example}"

            return p_desc if p_desc else "No description"

        try:
            local_tools = await self.process_local_tools(local_tools=local_tools)
            if local_tools:
                local_tools_list = local_tools.get_available_tools()
                if local_tools_list:
                    for tool in local_tools_list:
                        if isinstance(tool, dict):
                            name = tool.get("name", "unknown")
                            desc = (
                                tool.get("description", "").replace("\n", " ").strip()
                            )
                            lines.append(f"\n{name}: {desc}")
                            input_schema = tool.get("inputSchema", {})
                            params = input_schema.get("properties", {})
                            required = input_schema.get("required", [])
                            if params:
                                for param_name, param_info in params.items():
                                    p_type = format_param_type(param_info)
                                    p_desc = format_param_description(param_info)
                                    is_req = (
                                        " (required)" if param_name in required else ""
                                    )
                                    lines.append(
                                        f"  - {param_name}: {p_type}{is_req} — {p_desc}"
                                    )

            if mcp_tools and not self.enable_advanced_tool_use:
                for server_name, tools in mcp_tools.items():
                    if not tools:
                        continue
                    for tool in tools:
                        if hasattr(tool, "name"):
                            name = str(tool.name)
                            desc = str(tool.description).replace("\n", " ").strip()
                            lines.append(f"\n{name}: {desc}")
                            if hasattr(tool, "inputSchema") and tool.inputSchema:
                                params = tool.inputSchema.get("properties", {})
                                required = tool.inputSchema.get("required", [])
                                for param_name, param_info in params.items():
                                    p_type = format_param_type(param_info)
                                    p_desc = format_param_description(param_info)
                                    is_req = (
                                        " (required)" if param_name in required else ""
                                    )
                                    lines.append(
                                        f"  - {param_name}: {p_type}{is_req} — {p_desc}"
                                    )

            if len(lines) == 1:
                return "No tools available"
        except Exception as e:
            logger.error(f"Error building compact tool registry: {e}")
            return "No tools available"

        return "\n".join(lines)

    async def prepare_initial_messages(
        self,
        session_state,
        system_prompt: str,
        session_id: str,
        llm_connection: Callable,
        message_history: Callable[[], Any],
        mcp_tools: dict = None,
        local_tools: Any = None,
        debug: bool = False,
        sub_agents: list = None,
    ) -> None:
        """
        Prepare the full initial message list for the LLM by concurrently:
        - Building tool registry
        - Loading prior message history
        - Injecting current user query
        """
        tasks = {}

        tasks["tools"] = self.get_tools_registry(
            mcp_tools=mcp_tools, local_tools=local_tools
        )

        tasks["history"] = self.update_llm_working_memory(
            message_history=message_history,
            session_id=session_id,
            llm_connection=llm_connection,
            debug=debug,
        )

        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    tasks["tools"],
                    tasks["history"],
                    return_exceptions=True,
                ),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout during initial message preparation (20s). Proceeding with defaults."
            )
            results = ["No tools available", None]

        for r in results:
            if isinstance(r, BaseException):
                logger.error(f"prepare_initial_messages error: {r}", exc_info=True)

        tools_section = (
            results[0]
            if not isinstance(results[0], BaseException)
            else "No tools available"
        )

        updated_system_prompt = system_prompt

        if self.enable_advanced_tool_use:
            updated_system_prompt += f"\n{tools_retriever_additional_prompt}"

        if self.enable_agent_skills and self.skill_manager:
            updated_system_prompt += f"\n{agent_skills_additional_prompt}"

        if sub_agents:
            updated_system_prompt += f"\n{sub_agents_additional_prompt}"

        if self.memory_tool_backend:
            updated_system_prompt += f"\n{memory_tool_additional_prompt}"

        if self.enable_agent_skills and self.skill_manager:
            skills_context = self.skill_manager.get_skills_context_xml()
            if skills_context:
                updated_system_prompt += f"\n[AVAILABLE SKILLS]\n{skills_context}"

        if sub_agents:
            sub_agents_registry = await self.sub_agents_registry(sub_agents)
            updated_system_prompt += (
                f"\n[AVAILABLE SUB AGENTS REGISTRY]\n{sub_agents_registry}"
            )

        updated_system_prompt += f"\n[AVAILABLE TOOLS REGISTRY]\n{tools_section}"

        session_state.messages.insert(
            0, Message(role="system", content=updated_system_prompt)
        )

    async def sub_agents_registry(self, sub_agents: List[Any]) -> str:
        """
        Compact JSON-based registry format.
        More concise while maintaining all necessary information.
        """
        if not sub_agents:
            return "No sub-agents available."

        registry = []

        for agent in sub_agents:
            try:
                sig = inspect.signature(agent.run)

                parameters = {}
                for param_name, param in sig.parameters.items():
                    if param_name == "self":
                        continue

                    is_required = param.default is inspect.Parameter.empty
                    param_type = "any"
                    if param.annotation != inspect.Parameter.empty:
                        param_type = (
                            param.annotation.__name__
                            if hasattr(param.annotation, "__name__")
                            else str(param.annotation)
                        )

                    parameters[param_name] = {
                        "type": param_type,
                        "required": is_required,
                        "default": None if is_required else param.default,
                    }

                registry.append(
                    {
                        "agent_name": agent.name,
                        "description": agent.system_instruction,
                        "parameters": parameters,
                    }
                )

            except Exception as e:
                logger.error(
                    f"Error processing agent {getattr(agent, 'name', 'unknown')}: {e}"
                )

        output_lines = [
            "════════════════════════════════════════════════════════════",
            "AVAILABLE SUB-AGENTS REGISTRY",
            "════════════════════════════════════════════════════════════",
            "",
        ]

        for idx, agent_info in enumerate(registry, 1):
            output_lines.append(f"[{idx}] {agent_info['agent_name']}")
            output_lines.append(f"    Description: {agent_info['description']}")

            if agent_info["parameters"]:
                output_lines.append("    Parameters:")
                for param_name, param_details in agent_info["parameters"].items():
                    req_str = "REQUIRED" if param_details["required"] else "optional"
                    default_str = (
                        f", default={param_details['default']}"
                        if not param_details["required"]
                        else ""
                    )
                    output_lines.append(
                        f"      • {param_name}: {param_details['type']} ({req_str}{default_str})"
                    )
            else:
                output_lines.append("    Parameters: None")

            output_lines.append("")

        return "\n".join(output_lines)

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
        event = Event(
            type=EventType.SUB_AGENT_CALL_STARTED,
            payload=SubAgentCallStartedPayload(
                agent_name=self.agent_name,
                session_id=session_id,
                timestamp=str(datetime.now()),
                run_count=0,
                kwargs={"agent_calls": agent_calls},
            ),
            agent_name=self.agent_name,
        )
        if event_router:
            self.background_task_manager.run_background_strict(
                event_router(session_id=session_id, event=event)
            )
        metadata = {"agent_calls": agent_calls}
        await add_message_to_history(
            role="assistant",
            content=response,
            metadata=metadata,
            session_id=session_id,
        )
        session_state.messages.append(Message(role="assistant", content=response))

        if isinstance(agent_calls, str):
            agent_calls = json.loads(agent_calls)

        async def execute_single_agent(call: dict) -> tuple[str, Any]:
            """Execute a single agent, handling MCP connection if needed."""
            agent_name = call.get("agent")
            if not agent_name:
                raise ValueError("agent_call missing 'agent' field")

            try:
                agent = resolve_agent(agent_name, sub_agents)
                params = call.get("parameters", {})
                params["session_id"] = session_id
                kwargs = build_kwargs(agent, params)

                if hasattr(agent, "mcp_tools") and agent.mcp_tools:
                    logger.info(f"Connecting MCP servers for {agent_name}...")
                    await agent.connect_mcp_servers()

                logger.info(f"Running sub-agent: {agent_name}")
                result = await agent.run(**kwargs)
                await agent.cleanup_mcp_servers()
                return agent_name, result

            except Exception as e:
                logger.error(f"Error executing agent {agent_name}: {e}", exc_info=True)
                return agent_name, e

        logger.info(
            f"Executing {len(agent_calls)} sub-agents with concurrent MCP connections..."
        )
        tasks = [execute_single_agent(call) for call in agent_calls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        observations = []

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Unexpected top-level exception: {result}")
                obs = {
                    "agent_name": "unknown",
                    "status": "error",
                    "output": str(result),
                }
                observations.append(obs)
            else:
                agent_name, obs_data = result

                if isinstance(obs_data, Exception):
                    logger.error(f"Agent {agent_name} execution failed: {obs_data}")
                    obs = {
                        "agent_name": agent_name,
                        "status": "error",
                        "output": str(obs_data),
                    }
                    observations.append(obs)
                    event = Event(
                        type=EventType.SUB_AGENT_CALL_ERROR,
                        payload=SubAgentCallErrorPayload(
                            agent_name=agent_name,
                            session_id=session_id,
                            timestamp=str(datetime.now()),
                            error=str(obs_data),
                            error_count=0,
                        ),
                        agent_name=self.agent_name,
                    )
                    if event_router:
                        self.background_task_manager.run_background_strict(
                            event_router(session_id=session_id, event=event)
                        )
                else:
                    if isinstance(obs_data, dict):
                        agent_response = obs_data.get(
                            "response", obs_data.get("output", str(obs_data))
                        )
                        obs = {
                            "agent_name": agent_name,
                            "status": "success",
                            "output": agent_response,
                        }
                    elif isinstance(obs_data, str):
                        obs = {
                            "agent_name": agent_name,
                            "status": "success",
                            "output": obs_data,
                        }
                    else:
                        obs = {
                            "agent_name": agent_name,
                            "status": "success",
                            "output": str(obs_data),
                        }

                    logger.info(f"Agent {agent_name} completed successfully")
                    observations.append(obs)
                    if isinstance(obs_data, dict):
                        sub_usage = obs_data.get("metric")
                        if sub_usage and isinstance(sub_usage, Usage):
                            run_usage.incr(sub_usage)
                            usage.incr(sub_usage)

                    event = Event(
                        type=EventType.SUB_AGENT_CALL_RESULT,
                        payload=SubAgentCallResultPayload(
                            agent_name=agent_name,
                            session_id=session_id,
                            timestamp=str(datetime.now()),
                            run_count=0,
                            result=obs_data,
                        ),
                        agent_name=self.agent_name,
                    )
                    if event_router:
                        self.background_task_manager.run_background_strict(
                            event_router(session_id=session_id, event=event)
                        )

        xml_obs_block = build_sub_agents_observation_xml(observations)
        agent_call_result = {
            "agent_name": self.agent_name,
            "agent_calls": agent_calls,
            "output": observations,
        }

        if debug:
            show_sub_agent_call_result(agent_call_result)

        session_state.messages.append(Message(role="user", content=xml_obs_block))
        await add_message_to_history(
            role="user",
            content=xml_obs_block,
            session_id=session_id,
            metadata={"agent_name": self.agent_name, "sub_agent_results": True},
        )

    @track("agent_execution")
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
        session_state = self._get_session_state(session_id=session_id, debug=debug)
        session_state.messages = []
        session_state.assistant_with_tool_calls = None
        session_state.pending_tool_responses = []
        session_state.loop_detector.reset()
        start_time = time.perf_counter()
        run_usage = Usage()

        event = Event(
            type=EventType.USER_MESSAGE,
            payload=UserMessagePayload(
                message=query,
            ),
            agent_name=self.agent_name,
        )
        if event_router:
            self.background_task_manager.run_background_strict(
                event_router(session_id=session_id, event=event)
            )

        await add_message_to_history(
            role="user",
            content=query,
            session_id=session_id,
            metadata={"agent_name": self.agent_name},
        )
        await self.prepare_initial_messages(
            system_prompt=system_prompt,
            session_state=session_state,
            llm_connection=llm_connection,
            message_history=message_history,
            mcp_tools=mcp_tools,
            local_tools=local_tools,
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
                if debug:
                    logger.info(
                        f"Sending {len(session_state.messages)} messages to LLM"
                    )
                current_steps += 1
                if self._limits_enabled:
                    self.usage_limits.check_before_request(usage=usage)

                try:

                    @track("llm_call")
                    async def make_llm_call():
                        return await llm_connection.llm_call(session_state.messages)

                    response = await make_llm_call()

                    if response:
                        event = Event(
                            type=EventType.AGENT_MESSAGE,
                            payload=AgentMessagePayload(
                                message=str(response),
                            ),
                            agent_name=self.agent_name,
                        )
                        if event_router:
                            self.background_task_manager.run_background_strict(
                                event_router(session_id=session_id, event=event)
                            )

                        if hasattr(response, "usage"):
                            request_usage = Usage(
                                requests=1,
                                request_tokens=response.usage.prompt_tokens,
                                response_tokens=response.usage.completion_tokens,
                                total_tokens=response.usage.total_tokens,
                            )
                            usage.incr(request_usage)
                            run_usage.incr(request_usage)

                            if self._limits_enabled:
                                self.usage_limits.check_tokens(usage)
                                remaining_tokens = self.usage_limits.remaining_tokens(
                                    usage
                                )
                                used_tokens = usage.total_tokens
                                used_requests = usage.requests
                                remaining_requests = self.request_limit - used_requests
                                session_stats.update(
                                    {
                                        "used_requests": used_requests,
                                        "used_tokens": used_tokens,
                                        "remaining_requests": remaining_requests,
                                        "remaining_tokens": remaining_tokens,
                                        "request_tokens": request_usage.request_tokens,
                                        "response_tokens": request_usage.response_tokens,
                                        "total_tokens": request_usage.total_tokens,
                                    }
                                )
                                if debug:
                                    logger.info(
                                        f"API Call Stats - Requests: {used_requests}/{self.request_limit}, "
                                        f"Tokens: {used_tokens}/{self.usage_limits.total_tokens_limit}, "
                                        f"Request Tokens: {request_usage.request_tokens}, "
                                        f"Response Tokens: {request_usage.response_tokens}, "
                                        f"Total Tokens: {request_usage.total_tokens}, "
                                        f"Remaining Requests: {remaining_requests}, "
                                        f"Remaining Tokens: {remaining_tokens}"
                                    )
                        if hasattr(response, "choices"):
                            response = response.choices[0].message.content.strip()
                        elif hasattr(response, "message"):
                            response = response.message.content.strip()
                    else:
                        raise Exception("No response from LLM")
                except UsageLimitExceeded as e:
                    error_message = f"Usage limit error: {e}"
                    logger.error(error_message)
                    return {"answer": error_message, "usage": run_usage}

                except Exception as e:
                    error_message = f"LLM error: {e}"
                    logger.error(e)
                    return {"answer": error_message, "usage": run_usage}

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

                    session_state.messages.append(
                        Message(
                            role="assistant",
                            content=parsed_response.answer,
                        )
                    )

                    event = Event(
                        type=EventType.FINAL_ANSWER,
                        payload=FinalAnswerPayload(
                            message=str(parsed_response.answer),
                        ),
                        agent_name=self.agent_name,
                    )
                    if event_router:
                        self.background_task_manager.run_background_strict(
                            event_router(session_id=session_id, event=event)
                        )
                    await add_message_to_history(
                        role="assistant",
                        content=parsed_response.answer,
                        session_id=session_id,
                        metadata={"agent_name": self.agent_name},
                    )

                    session_state.state = AgentState.FINISHED
                    run_usage.total_time = time.perf_counter() - start_time
                    return {"answer": parsed_response.answer, "usage": run_usage}

                if parsed_response.action is not None:
                    if parsed_response.agent_calls is not None:
                        agent_calls = parsed_response.data

                        @track("sub_agent_action_execution")
                        async def execute_sub_agent_calls():
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

                        await execute_sub_agent_calls()
                    else:

                        @track("action_execution")
                        async def execute_action():
                            await self.act(
                                parsed_response=parsed_response,
                                response=response,
                                add_message_to_history=add_message_to_history,
                                system_prompt=system_prompt,
                                mcp_tools=mcp_tools,
                                debug=debug,
                                sessions=sessions,
                                local_tools=local_tools,
                                session_id=session_id,
                                event_router=event_router,
                                sub_agents=sub_agents,
                            )

                        await execute_action()

                if parsed_response.error is not None:
                    logger.error(f"Error in parsed response: {parsed_response.error}")
                    session_state.messages.append(
                        Message(
                            role="user",
                            content=(
                                f"{parsed_response.error}\n\n"
                                "Error in your response parsing. Please follow the response format strictly. "
                                "If the issue persists, provide a final answer to the user and stop."
                            ),
                        )
                    )
                    continue
                if current_steps >= self.max_steps:
                    session_state.state = AgentState.STUCK
                    if last_valid_response:
                        max_steps_context = f"[SYSTEM_CONTEXT: MAX_STEPS_REACHED - Agent hit {self.max_steps} step limit]\n\n"
                        return {
                            "answer": max_steps_context + last_valid_response,
                            "usage": run_usage,
                        }

                    else:
                        return {
                            "answer": f"[SYSTEM_CONTEXT: MAX_STEPS_REACHED - Agent hit {self.max_steps} step limit without valid response]",
                            "usage": run_usage,
                        }

        if session_state.state == AgentState.STUCK and last_valid_response:
            loop_context = (
                "[SYSTEM_CONTEXT: LOOP_DETECTED - Agent stuck in tool call loop]\n\n"
            )
            return loop_context + last_valid_response

        run_usage.total_time = time.perf_counter() - start_time
        return {"answer": last_valid_response, "usage": run_usage}
