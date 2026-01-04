import json
from pathlib import Path
from datetime import datetime, timezone
import time
from typing import List, Dict, Any

from omnicoreagent import ToolRegistry, MemoryRouter, OmniCoreAgent, EventRouter
from agents.system_prompts import rule_checker_agent_prompt

# Local tools registry
local_tools = ToolRegistry()

RULES_PATH = Path("agents/data/plan_rules.json")


def load_plan_rules() -> Dict[str, Any]:
    """Load plan rules from disk; return empty structure if missing."""
    if RULES_PATH.exists():
        try:
            with open(RULES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[RuleChecker] Error loading rules path data: {e}")
            return {}
    return {}


@local_tools.register_tool(
    name="rule_violation_logger",
    description="Log a detected medical claim rule violation to the audit database or report",
    inputSchema={
        "type": "object",
        "properties": {
            "claim_id": {"type": "string"},
            "violation_id": {"type": "string"},
            "rule_id": {"type": "string"},
            "description": {"type": "string"},
            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
            "recommended_action": {"type": "string"},
            "recommended_recovery": {"type": "number"},
        },
        "required": ["claim_id", "violation_id", "rule_id", "description"],
        "additionalProperties": False,
    },
)
async def rule_violation_logger(
    claim_id: str,
    violation_id: str,
    rule_id: str,
    description: str,
    severity: str = "medium",
    recommended_action: str = None,
    recommended_recovery: float = 0.0,
) -> dict:
    """Store or log a single rule violation for later audit synthesis."""
    timestamp = datetime.now(timezone.utc).isoformat()
    violation = {
        "claim_id": claim_id,
        "violation_id": violation_id,
        "rule_id": rule_id,
        "description": description,
        "severity": severity,
        "recommended_action": recommended_action,
        "recommended_recovery": recommended_recovery,
        "timestamp": timestamp,
    }
    print(
        f"[{timestamp}] Logged violation for claim {claim_id}: {violation_id} - {rule_id} - {description}"
    )
    return violation


@local_tools.register_tool(
    name="check_claim_against_rules",
    description=(
        "Load the plan configuration and global rules for the given claim's plan_id. "
        "Returns raw plan data and rule definitions so the agent can apply deterministic checks."
    ),
    inputSchema={
        "type": "object",
        "properties": {"claim": {"type": "object"}},
        "required": ["claim"],
        "additionalProperties": False,
    },
)
async def check_claim_against_rules(claim: Dict[str, Any]) -> Dict[str, Any]:
    if not RULES_PATH.exists():
        return {"status": "error", "message": f"rules file not found at {RULES_PATH}"}

    try:
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            rules_data = json.load(f)
    except Exception as e:
        return {"status": "error", "message": f"failed to load rules: {e}"}

    plan_id = claim.get("plan_id", "PLAN_A")
    plan_config = rules_data["plans"].get(plan_id, {})
    global_rules = rules_data.get("global_rules", [])

    return {
        "status": "success",
        "data": {
            "plan_id": plan_id,
            "plan_config": plan_config,
            "global_rules": global_rules,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


class RuleCheckerAgent:
    def __init__(self):
        self.local_tools = local_tools
        self._mcp_servers_connected = False
        self._agent = None

    async def initialize_mcp_servers(self):
        """Initialize MCP servers once (call during lifespan)."""
        if not self._mcp_servers_connected:
            print("=" * 60)
            print("INITIALIZING: MCP Servers Connection (RuleCheckerAgent)")
            print("=" * 60)

            memory_router = MemoryRouter(memory_store_type="in_memory")
            event_router = EventRouter(event_store_type="in_memory")

            self._agent = OmniCoreAgent(
                name="rule_checker_agent",
                system_instruction=rule_checker_agent_prompt,
                agent_config={
                    "max_steps": 20,
                    "tool_call_timeout": 600,
                    # Memory with summarization for long claim histories
                    "memory_config": {
                        "mode": "token_budget",
                        "value": 10000,
                        "summary": {
                            "enabled": True,
                            "retention_policy": "summarize",
                        },
                    },
                    # Context management for complex multi-step audits
                    "context_management": {
                        "enabled": True,
                        "mode": "token_budget",
                        "value": 80000,
                        "threshold_percent": 70,
                        "strategy": "summarize_and_truncate",
                        "preserve_recent": 6,
                    },
                    # Guardrails for audit integrity
                    "guardrail_config": {
                        "enabled": True,
                        "strict_mode": True,
                        "fail_action": "block",
                    },
                },
                model_config={
                    "provider": "openai",
                    "model": "gpt-4.1",
                    "max_context_length": 5000,
                },
                local_tools=self.local_tools,
                memory_router=memory_router,
                event_router=event_router,
                debug=True,
            )

            await self._agent.connect_mcp_servers()
            self._mcp_servers_connected = True
            print("MCP Servers connected for RuleCheckerAgent!")

    async def run(self, claim_data: dict, session_id: str):
        """Run the rule-checking workflow."""
        if not self._mcp_servers_connected:
            await self.initialize_mcp_servers()

        print("=" * 60)
        print("RUNNING: RuleCheckerAgent on claim data")
        print("=" * 60)

        start_llm = time.perf_counter()
        llm_result = await self._agent.run(
            query=f"<context>{json.dumps(claim_data)}</context>", session_id=session_id
        )
        end_llm = time.perf_counter()
        print(f"LLM pass finished in {end_llm - start_llm:.2f}s")
        return llm_result

    async def stream_events(self, session_id):
        async for event in self._agent.stream_events(session_id):
            yield event

    async def cleanup_mcp_servers(self):
        if self._mcp_servers_connected:
            await self._agent.cleanup()
            self._mcp_servers_connected = False
            self._agent = None
            print("MCP Servers disconnected (RuleCheckerAgent)!")
