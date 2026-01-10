import json
from datetime import datetime, timezone
import time
from typing import Dict, Any

from omnicoreagent import ToolRegistry, MemoryRouter, OmniCoreAgent, EventRouter
from agents.system_prompts import appeal_agent_prompt

# Local tools registry
local_tools = ToolRegistry()


@local_tools.register_tool(
    name="send_appeal_email",
    description="Send a denial/appeal letter to the provider via email. For prototype, logs to console.",
    inputSchema={
        "type": "object",
        "properties": {
            "to_email": {"type": "string", "description": "Provider's email address"},
            "claim_id": {"type": "string"},
            "subject": {"type": "string"},
            "body_html": {"type": "string", "description": "Full HTML email body"},
            "audit_packet": {
                "type": "object",
                "description": "Final audit packet for reference",
            },
        },
        "required": ["to_email", "claim_id", "subject", "body_html", "audit_packet"],
        "additionalProperties": False,
    },
)
async def send_appeal_email(
    to_email: str,
    claim_id: str,
    subject: str,
    body_html: str,
    audit_packet: Dict[str, Any],
) -> dict:
    """Stub: simulate sending email. In production, integrate with email service."""
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"\n📧 [EMAIL SIMULATION] Sent to: {to_email}")
    print(f"   Subject: {subject}")
    print(f"   Claim ID: {claim_id}")
    print(f"   Timestamp: {timestamp}")
    # In real system we will be sending  via SMTP, SendGrid, etc.
    return {
        "status": "success",
        "message": "Email sent successfully (simulated)",
        "timestamp": timestamp,
        "recipient": to_email,
        "claim_id": claim_id,
    }


class AppealAgent:
    def __init__(self):
        self.local_tools = local_tools
        self._mcp_servers_connected = False
        self._agent = None

    async def initialize_mcp_servers(self):
        """Initialize MCP servers once (call during lifespan)."""
        if not self._mcp_servers_connected:
            print("=" * 60)
            print("INITIALIZING: MCP Servers Connection (AppealAgent)")
            print("=" * 60)

            memory_router = MemoryRouter(memory_store_type="in_memory")
            event_router = EventRouter(event_store_type="in_memory")

            self._agent = OmniCoreAgent(
                name="appeal_agent",
                system_instruction=appeal_agent_prompt,
                agent_config={
                    "max_steps": 8,
                    "tool_call_timeout": 300,
                    "memory_config": {"mode": "token_budget", "value": 10000},
                },
                model_config={
                    "provider": "openai",
                    "model": "gpt-4.1",
                    "max_context_length": 8000,
                },
                local_tools=self.local_tools,
                memory_router=memory_router,
                event_router=event_router,
                debug=True,
            )

            await self._agent.connect_mcp_servers()
            self._mcp_servers_connected = True
            print("MCP Servers connected for AppealAgent!")

    async def run(
        self,
        claim: Dict[str, Any],
        final_audit_xml: str,
        provider_email: str,
        session_id: str,
    ):
        """
        Generate and send a denial/appeal letter to the provider.
        """
        if not self._mcp_servers_connected:
            await self.initialize_mcp_servers()

        print("=" * 60)
        print("RUNNING: AppealAgent to generate denial letter")
        print("=" * 60)

        context = {
            "claim": claim,
            "final_audit_xml": final_audit_xml,
            "provider_email": provider_email,
        }

        start_llm = time.perf_counter()
        llm_result = await self._agent.run(
            query=f"<context>{json.dumps(context)}</context>",
            session_id=session_id,
        )
        end_llm = time.perf_counter()
        print(f"Appeal letter generation finished in {end_llm - start_llm:.2f}s")
        return llm_result

    async def stream_events(self, session_id):
        async for event in self._agent.stream_events(session_id):
            yield event

    async def cleanup_mcp_servers(self):
        if self._mcp_servers_connected:
            await self._agent.cleanup()
            self._mcp_servers_connected = False
            self._agent = None
            print("MCP Servers disconnected (AppealAgent)!")
