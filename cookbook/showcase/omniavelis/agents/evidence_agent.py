import json
from pathlib import Path
from datetime import datetime, timezone
import time
from typing import Dict, Any, List
import re
from omnicoreagent import ToolRegistry, MemoryRouter, OmniCoreAgent, EventRouter
from agents.system_prompts import evidence_agent_prompt

# Local tools registry
local_tools = ToolRegistry()

EVIDENCE_MD_PATH = Path("agents/data/plan_rules.md")


def parse_evidence_snippets(plan_id: str, rule_ids: List[str]) -> List[Dict[str, Any]]:
    """Parse plan_rules.md and extract snippets for given plan + rules."""
    if not EVIDENCE_MD_PATH.exists():
        return [
            {
                "source_id": "MISSING_EVIDENCE_FILE",
                "snippet": "Evidence source file not found.",
                "relevance_score": 0.0,
                "url_status": "unavailable",
            }
        ]

    content = EVIDENCE_MD_PATH.read_text(encoding="utf-8")

    plan_sections = re.split(r"^##\s+(PLAN_[A-Z0-9]+):", content, flags=re.MULTILINE)[
        1:
    ]

    plan_content = ""
    for i in range(0, len(plan_sections), 2):
        pid = plan_sections[i].strip()
        text = plan_sections[i + 1]
        if pid == plan_id:
            plan_content = text
            break

    items = []
    for rule_id in rule_ids:
        pattern = rf"^###\s+({rule_id}):?\s+(.+?)$"
        match = re.search(pattern, plan_content, re.MULTILINE | re.DOTALL)

        if match:
            title = match.group(2).strip()
            start = match.start()
            next_heading = re.search(
                r"\n###\s+[A-Z0-9_]+:", plan_content[start + 1 :], re.MULTILINE
            )
            end = (
                start
                + 1
                + (next_heading.start() if next_heading else len(plan_content))
            )
            full_block = plan_content[start:end].strip()

            source_id_match = re.search(r"\*\*Source ID\*\*:\s+`([^`]+)`", full_block)
            source_id = (
                source_id_match.group(1) if source_id_match else f"{plan_id}_{rule_id}"
            )

            snippet_lines = []
            for line in full_block.split("\n"):
                if line.startswith("### ") or "**Source ID**:" in line:
                    continue
                snippet_lines.append(line.strip())
            snippet = " ".join([line for line in snippet_lines if line]).replace(
                "  ", " "
            )

            items.append(
                {
                    "source_id": source_id,
                    "snippet": snippet or title,
                    "relevance_score": 0.95,
                    "url_status": "available",
                }
            )
        else:
            global_match = re.search(
                rf"^###\s+({rule_id}):?\s+(.+?)$", content, re.MULTILINE | re.DOTALL
            )
            if global_match:
                title = global_match.group(2).strip()
                items.append(
                    {
                        "source_id": f"GLOBAL_{rule_id}",
                        "snippet": title,
                        "relevance_score": 0.90,
                        "url_status": "available",
                    }
                )
            else:
                items.append(
                    {
                        "source_id": rule_id,
                        "snippet": f"No evidence found for {rule_id} under {plan_id}.",
                        "relevance_score": 0.50,
                        "url_status": "unavailable",
                    }
                )

    return items


@local_tools.register_tool(
    name="fetch_plan_rule_snippets",
    description="Extract policy evidence snippets from plan_rules.md for given rule IDs and plan.",
    inputSchema={
        "type": "object",
        "properties": {
            "claim_id": {"type": "string"},
            "rule_ids": {"type": "array", "items": {"type": "string"}},
            "plan_id": {"type": "string"},
        },
        "required": ["claim_id", "rule_ids", "plan_id"],
        "additionalProperties": False,
    },
)
async def fetch_plan_rule_snippets(
    claim_id: str,
    rule_ids: List[str],
    plan_id: str,
) -> Dict[str, Any]:
    items = parse_evidence_snippets(plan_id, rule_ids)
    for item in items:
        item["claim_id"] = claim_id
        item["source_type"] = "plan_rule"
        item["url"] = ""
    return {"status": "success", "data": items}


class EvidenceAgent:
    def __init__(self):
        self.local_tools = local_tools
        self._mcp_servers_connected = False
        self._agent = None

    async def initialize_mcp_servers(self):
        """Initialize MCP servers once (call during lifespan)."""
        if not self._mcp_servers_connected:
            print("=" * 60)
            print("INITIALIZING: MCP Servers Connection (EvidenceAgent)")
            print("=" * 60)

            memory_router = MemoryRouter(memory_store_type="in_memory")
            event_router = EventRouter(event_store_type="in_memory")

            self._agent = OmniCoreAgent(
                name="evidence_agent",
                system_instruction=evidence_agent_prompt,
                agent_config={
                    "max_steps": 10,
                    "tool_call_timeout": 300,
                    "memory_config": {"mode": "token_budget", "value": 8000},
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
            print("MCP Servers connected for EvidenceAgent!")

    async def run(self, evidence_request: dict, session_id: str):
        """
        evidence_request should contain:
        - claim_id
        - rule_ids: list of violated rule IDs
        - plan_id (optional, but recommended)
        - supporting_docs: optional list of URLs (not used yet)
        """
        if not self._mcp_servers_connected:
            await self.initialize_mcp_servers()

        print("=" * 60)
        print("RUNNING: EvidenceAgent to fetch justification snippets")
        print("=" * 60)

        start_llm = time.perf_counter()
        llm_result = await self._agent.run(
            query=f"<context>{json.dumps(evidence_request)}</context>",
            session_id=session_id,
        )
        end_llm = time.perf_counter()
        print(f"Evidence retrieval finished in {end_llm - start_llm:.2f}s")
        return llm_result

    async def stream_events(self, session_id):
        async for event in self._agent.stream_events(session_id):
            yield event

    async def cleanup_mcp_servers(self):
        if self._mcp_servers_connected:
            await self._agent.cleanup()
            self._mcp_servers_connected = False
            self._agent = None
            print("MCP Servers disconnected (EvidenceAgent)!")
