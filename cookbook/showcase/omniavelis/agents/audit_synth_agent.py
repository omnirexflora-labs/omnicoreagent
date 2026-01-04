import json
import time
from typing import Dict, Any

from omnicoreagent import MemoryRouter, OmniCoreAgent, EventRouter
from agents.system_prompts import audit_synth_agent_prompt


class AuditSynthAgent:
    def __init__(self):
        self._mcp_servers_connected = False
        self._agent = None

    async def initialize_mcp_servers(self):
        """Initialize MCP servers once (call during lifespan)."""
        if not self._mcp_servers_connected:
            print("=" * 60)
            print("INITIALIZING: MCP Servers Connection (AuditSynthAgent)")
            print("=" * 60)

            memory_router = MemoryRouter(memory_store_type="in_memory")
            event_router = EventRouter(event_store_type="in_memory")

            self._agent = OmniCoreAgent(
                name="audit_synth_agent",
                system_instruction=audit_synth_agent_prompt,
                agent_config={
                    "max_steps": 5,
                    "tool_call_timeout": 300,
                    "memory_config": {"mode": "token_budget", "value": 6000},
                },
                model_config={
                    "provider": "openai",
                    "model": "gpt-4.1",
                    "max_context_length": 5000,
                },
                memory_router=memory_router,
                event_router=event_router,
                debug=True,
            )

            await self._agent.connect_mcp_servers()
            self._mcp_servers_connected = True
            print("MCP Servers connected for AuditSynthAgent!")

    async def run(
        self,
        claim: Dict[str, Any],
        violations_xml: str,
        evidence_xml: str,
        session_id: str,
    ):
        """
        Synthesize final audit packet from rule checker + evidence outputs.
        """
        if not self._mcp_servers_connected:
            await self.initialize_mcp_servers()

        print("=" * 60)
        print("RUNNING: AuditSynthAgent to generate final audit packet")
        print("=" * 60)

        # Build context
        context = {
            "claim": claim,
            "violations_xml": violations_xml,
            "evidence_xml": evidence_xml,
            "session_id": session_id,
        }

        start_llm = time.perf_counter()
        llm_result = await self._agent.run(
            query=f"<context>{json.dumps(context)}</context>",
            session_id=session_id,
        )
        end_llm = time.perf_counter()
        print(f"Audit synthesis finished in {end_llm - start_llm:.2f}s")
        return llm_result

    async def stream_events(self, session_id):
        async for event in self._agent.stream_events(session_id):
            yield event

    async def cleanup_mcp_servers(self):
        if self._mcp_servers_connected:
            await self._agent.cleanup()
            self._mcp_servers_connected = False
            self._agent = None
            print("MCP Servers disconnected (AuditSynthAgent)!")
