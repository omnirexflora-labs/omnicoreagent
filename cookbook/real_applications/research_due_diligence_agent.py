"""Research due diligence agent built on OmniCoreAgent.

The app supplies domain tools. OmniCoreAgent supplies the harness: model loop,
parallel tool batches, workspace files, tool offloading, and telemetry traces.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from omnicoreagent import MemoryRouter, OmniCoreAgent, ToolRegistry

try:
    from cookbook.real_applications._bootstrap import (
        model_config,
        require_llm_api_key,
        response_text,
    )
except ModuleNotFoundError:
    from _bootstrap import model_config, require_llm_api_key, response_text


def build_research_tools() -> ToolRegistry:
    """Create deterministic research tools for the due diligence workflow."""
    tools = ToolRegistry()

    @tools.register_tool("company_profile")
    def company_profile(company: str) -> dict:
        return {
            "company": company,
            "sector": "AI infrastructure",
            "stage": "Series B",
            "team": 84,
            "revenue_band": "$10M-$25M",
            "customers": ["fintech", "developer tools", "health operations"],
        }

    @tools.register_tool("market_signals")
    def market_signals(company: str) -> dict:
        return {
            "company": company,
            "tailwinds": [
                "enterprise demand for agent infrastructure",
                "migration from demos to production agent systems",
                "budget moving toward governed automation",
            ],
            "headwinds": [
                "crowded developer tooling market",
                "long enterprise security review cycles",
            ],
        }

    @tools.register_tool("risk_register")
    def risk_register(company: str) -> dict:
        return {
            "company": company,
            "risks": [
                {"name": "platform dependency", "severity": "medium"},
                {"name": "enterprise sales cycle", "severity": "medium"},
                {"name": "model capability drift", "severity": "low"},
            ],
        }

    @tools.register_tool("evidence_pack")
    def evidence_pack(company: str) -> dict:
        lines = [
            f"{company} evidence line {index}: customer signal, market note, or diligence detail."
            for index in range(180)
        ]
        return {
            "company": company,
            "documents": "\n".join(lines),
            "source_count": len(lines),
        }

    return tools


def build_agent(
    workspace_dir: Path | str = "tmp/real_apps/due_diligence",
) -> OmniCoreAgent:
    return OmniCoreAgent(
        name="due_diligence_agent",
        system_instruction="""You are a due diligence analyst.
Use independent tools in the same batch when their inputs do not depend on each other.
Save durable notes and reports in workspace files. If a tool response is offloaded,
use artifact readback tools when the full evidence is needed.""",
        model_config=model_config(max_tokens=1200),
        local_tools=build_research_tools(),
        memory_router=MemoryRouter("in_memory"),
        agent_config={
            "max_steps": 12,
            "tool_call_timeout": 20,
            "enable_advanced_tool_use": True,
            "enable_workspace_files": True,
            "context_management": {
                "enabled": True,
                "mode": "token_budget",
                "value": 20000,
                "threshold_percent": 75,
                "strategy": "truncate",
            },
            "tool_offload": {
                "enabled": True,
                "threshold_tokens": 250,
                "threshold_bytes": 1200,
                "max_preview_tokens": 120,
            },
            "workspace_config": {
                "workspace_backend": "local",
                "workspace_dir": str(workspace_dir),
            },
        },
    )


async def main() -> None:
    require_llm_api_key()

    agent = build_agent()
    result = await agent.run(
        "Run due diligence on OmniRetail AI. Gather profile, market signals, risk register, "
        "and the evidence pack together where possible. Save a markdown memo at "
        "reports/omniretail-diligence.md and return the investment view.",
        session_id="real_app_due_diligence",
    )

    print(response_text(result))
    print(f"trace_id={result['trace_id']}")
    print(f"run_id={result['run_id']}")
    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
