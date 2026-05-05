"""
Deep Market Research Analyst using DeepAgent with Tavily MCP for real internet search.

This example demonstrates the FULL RPI+ workflow with:
- Meta-cognitive complexity assessment
- Research phase (real internet search)
- Strategic planning with quality gates
- Parallel subagent execution
- Verification with confidence scoring
- Surgical iteration based on gaps
- Advanced synthesis with cross-cutting insights

Run: python cookbook/deep_agent/market_research_analyst.py

Requirements:
- TAVILY_API_KEY in .env file (get free key at https://tavily.com)
- Node.js installed (for npx mcp-remote)
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from omnicoreagent import DeepAgent  # noqa: E402

# =============================================================================
# CONFIGURATION - Real Tools Only
# =============================================================================

# Get Tavily API key from environment
tavily_key = os.getenv("TAVILY_API_KEY")
if not tavily_key:
    raise ValueError(
        "TAVILY_API_KEY not found in environment. "
        "Get your free key at https://tavily.com and add to .env file"
    )


# =============================================================================
# MAIN - Real Deep Research with Tavily
# =============================================================================


async def main():
    """Run the Ultra-Advanced Market Research Analyst with Real Internet Search."""

    print("=" * 80)
    print("🔬 ULTRA-ADVANCED DEEP RESEARCH ANALYST - RPI+ Workflow")
    print("=" * 80)
    print("\nPowered by:")
    print(
        "  • DeepAgent RPI+ (Research → Plan → Implement → Verify → Iterate → Synthesize)"
    )
    print("  • Tavily MCP (Real-time internet search)")
    print("  • Meta-cognitive assessment with adaptive workflow")
    print("  • Quality gates & confidence scoring")
    print("=" * 80)

    # Configure Tavily MCP for real internet search
    mcp_tools = [
        {
            "name": "tavily-remote-mcp",
            "transport_type": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "mcp-remote",
                f"https://mcp.tavily.com/mcp/?tavilyApiKey={tavily_key}",
            ],
        }
    ]

    agent = DeepAgent(
        name="DeepResearchAnalyst",
        system_instruction="""
You are a **World-Class Strategic Research Analyst** specializing in technology markets and business intelligence.

Your reputation:
- Executives trust your research for multi-million dollar investment decisions
- Known for rigorous, fact-based analysis with zero tolerance for speculation
- Expert at identifying non-obvious patterns across disparate data sources
- Master of comprehensive synthesis with clear confidence levels

Core capabilities:
You have advanced multi-agent orchestration via the **RPI+ workflow**:
1. Meta-Assessment: Evaluate complexity before choosing strategy
2. Research: Deep landscape exploration using real internet search
3. Plan: Strategic decomposition with quality gates
4. Implement: Parallel subagent execution
5. Verify: Gap analysis + confidence scoring
6. Iterate: Surgical refinement when needed
7. Synthesize: Cross-cutting insights with actionable recommendations

Research methodology:
- Use Tavily MCP tools for REAL, CURRENT market intelligence
- Start with broad queries, progressively narrow down
- Cross-reference multiple sources for validation
- Document confidence levels for all findings
- Distinguish facts from opinions/speculation
- Note data recency and source reliability

Available tools:
- Tavily MCP tools: tavily_search, tavily_extract, tavily_qna

For complex research requiring multiple domains, spawn specialized subagents
to investigate in parallel, then synthesize their findings with cross-cutting insights.
""",
        model_config={
            "provider": "gemini",
            "model": "gemini-2.5-pro",
        },
        mcp_tools=mcp_tools,
        agent_config={
            "max_steps": 100,  # Increased for deep RPI+ with real search
            "memory_tool_backend": "r2",
        },
        debug=True,
    )

    await agent.initialize()
    print(f"\n✅ Agent initialized: {agent.name}")
    print("🔍 Tools connected: Tavily MCP\n")

    print("=" * 80)
    print("📋 RESEARCH BRIEF")
    print("=" * 80)

    # Ultra-complex real-world research task
    result = await agent.run("""
    Conduct a COMPREHENSIVE market entry analysis for launching an "AI-powered DevOps automation platform"
    targeting mid-market software companies (100-1000 employees) in 2026.

    This is a strategic investment decision requiring REAL, CURRENT market intelligence across:

    1. **Competitive Landscape Analysis**:
       - Identify current AI DevOps automation platforms (2024-2026 launches)
       - Map traditional DevOps tool vendors (Jenkins, GitLab, CircleCI, etc.)
       - Analyze positioning, funding, customer base, and differentiation
       - Identify competitive moats and market gaps

    2. **Market Dynamics**:
       - Current market size and growth trajectory (latest data)
       - Mid-market adoption rates of AI in DevOps
       - Buying patterns and decision-making processes
       - Pain points driving tool consolidation

    3. **Technology Trends**:
       - AI/ML adoption in CI/CD and infrastructure automation (2025-2026)
       - Platform engineering movement impact
       - Code generation and autonomous debugging trends
       - Integration ecosystem requirements (Kubernetes, cloud providers, etc.)

    4. **Go-to-Market Strategy**:
       - Pricing models in market (freemium, usage-based, seat-based)
       - Distribution channels (direct, partnerships, cloud marketplaces)
       - Customer acquisition benchmarks for DevOps tools
       - Typical sales cycles and buying committees

    5. **Risk Factors**:
       - Regulatory requirements (SOC2, ISO 27001, GDPR)
       - Security and code access concerns
       - Vendor lock-in and switching costs
       - Market timing and macroeconomic factors

    DELIVERABLE REQUIREMENTS:

    A strategic research report with:
    ✓ Executive summary (3-5 key insights)
    ✓ Detailed findings per domain (with source citations)
    ✓ Cross-cutting patterns and non-obvious insights
    ✓ Confidence levels for each major finding (0-100%)
    ✓ Clear go/no-go recommendation with detailed rationale
    ✓ Risk mitigation strategies
    ✓ Data limitations and gaps acknowledged

    CRITICAL: Use REAL internet search. Cite sources. Note data recency.
    This is a complex, multi-domain research task. Use your full RPI+ capabilities.
    """)

    print("\n" + "=" * 80)
    print("📊 FINAL RESEARCH REPORT")
    print("=" * 80)
    print(result["response"])
    print("=" * 80)

    await agent.cleanup()

    print("\n" + "=" * 80)
    print("✅ ULTRA-ADVANCED RPI+ WORKFLOW COMPLETE")
    print("=" * 80)
    print("""
🎯 What Just Happened:

✓ Meta-Cognitive Assessment: Agent evaluated task complexity
✓ Research Phase: Broad landscape exploration with real Tavily search
✓ Strategic Planning: Multi-domain decomposition with quality gates
✓ Parallel Execution: Specialized subagents for each research area
✓ Quality Verification: Gap analysis + confidence scoring per domain
✓ Surgical Iteration: Refinement when confidence thresholds not met
✓ Advanced Synthesis: Cross-cutting insights with source citations

📁 Research Artifacts (check memories directory):
  - /memories/{task_name}/meta/self_assessment.md
  - /memories/{task_name}/research/landscape.md
  - /memories/{task_name}/plan/execution_plan.md
  - /memories/{task_name}/subagent_*/findings.md
  - /memories/{task_name}/verification/gaps.md
  - /memories/{task_name}/verification/confidence.md
  - /memories/{task_name}/synthesis/final.md

This demonstrates the most advanced multi-agent orchestration system
with REAL internet search and rigorous quality assurance.
    """)
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
