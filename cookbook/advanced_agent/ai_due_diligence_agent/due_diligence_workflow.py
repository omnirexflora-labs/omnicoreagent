#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                    AI DUE DILIGENCE WORKFLOW                                  ║
║               Powered by OmniCoreAgent Multi-Agent Orchestration              ║
╚═══════════════════════════════════════════════════════════════════════════════╝

A showcase application demonstrating how to orchestrate a complex, multi-agent
pipeline for startup investment analysis.

Pipeline Stages:
1. Company Research   - Gathers company intel via Tavily search
2. Market Analysis    - Analyzes TAM, competitors, positioning
3. Financial Modeling - Creates revenue projections with charts
4. Risk Assessment    - Evaluates investment risks
5. Investor Memo      - Synthesizes findings into a decision memo
6. Report Generator   - Formats memo as professional HTML
7. Infographic        - Creates a visual summary dashboard

Usage:
    python due_diligence_workflow.py "Company Name"
    python due_diligence_workflow.py --company "Agno AI"
    python due_diligence_workflow.py --help
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

from omnicoreagent import (
    OmniCoreAgent,
    ToolRegistry,
)

# Import the granular tool factories
from due_diligence_tools import (
    create_chart_tool,
    create_report_tool,
    create_infographic_tool,
)

# ─────────────────────────────────────────────────────────────────────────────
# Console Styling Utilities
# ─────────────────────────────────────────────────────────────────────────────


class Colors:
    """ANSI color codes for terminal output."""

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def print_header():
    """Print the application header."""
    print(f"""
{Colors.CYAN}╔═══════════════════════════════════════════════════════════════════════════════╗
║{Colors.BOLD}                    🔍 AI DUE DILIGENCE WORKFLOW                              {Colors.RESET}{Colors.CYAN}║
║{Colors.DIM}               Powered by OmniCoreAgent Multi-Agent Orchestration              {Colors.RESET}{Colors.CYAN}║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.RESET}
""")


def print_stage(stage_num: int, total: int, name: str, status: str = "running"):
    """Print a stage indicator."""
    icons = {
        "running": f"{Colors.YELLOW}⏳",
        "complete": f"{Colors.GREEN}✓",
        "error": f"{Colors.RED}✗",
    }
    icon = icons.get(status, icons["running"])
    progress = f"[{stage_num}/{total}]"
    print(f"\n{icon} {Colors.BOLD}{progress}{Colors.RESET} {name}{Colors.RESET}")


def print_summary(company: str, outputs: dict, elapsed: float):
    """Print the workflow summary."""
    print(f"""
{Colors.GREEN}╔═══════════════════════════════════════════════════════════════════════════════╗
║                           ✅ WORKFLOW COMPLETE                                ║
╚═══════════════════════════════════════════════════════════════════════════════╝{Colors.RESET}

{Colors.BOLD}Company:{Colors.RESET} {company}
{Colors.BOLD}Duration:{Colors.RESET} {elapsed:.1f} seconds

{Colors.BOLD}Generated Files:{Colors.RESET}
""")
    output_dir = os.path.join(os.getcwd(), "outputs")
    if os.path.exists(output_dir):
        files = sorted(
            os.listdir(output_dir),
            key=lambda x: os.path.getmtime(os.path.join(output_dir, x)),
            reverse=True,
        )[:5]
        for f in files:
            filepath = os.path.join(output_dir, f)
            size = os.path.getsize(filepath)
            if f.endswith(".html"):
                icon = "📄"
            elif f.endswith(".png"):
                icon = "🖼️ "
            else:
                icon = "📁"
            print(f"  {icon} {Colors.CYAN}{f}{Colors.RESET} ({size:,} bytes)")

    print(f"\n{Colors.DIM}Output directory: {output_dir}{Colors.RESET}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Logging Configuration
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("DueDiligence")


# ─────────────────────────────────────────────────────────────────────────────
# Agent Factory
# ─────────────────────────────────────────────────────────────────────────────


def create_agents(tavily_key: str, model: str = "gpt-4o", provider: str = "openai"):
    """Initializes all agents for the pipeline with refined system instructions."""

    # Shared Model Config
    model_config = {"provider": provider, "model": model}

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 1: Company Researcher
    # ─────────────────────────────────────────────────────────────────────────
    researcher = OmniCoreAgent(
        name="CompanyResearcher",
        system_instruction="""
You are a senior investment analyst conducting company research.

**RESEARCH STRATEGY:**
1. Search for key company information: Founded date, HQ, Team Size.
2. Identify the Founders and their backgrounds.
3. Understand the Core Product and Technology.
4. Dig into Funding History (Rounds, Investors, Amounts).
5. Look for Traction signals (Customers, Partnerships, Growth).

**FOR EARLY-STAGE STARTUPS:**
- Focus on the founders and the problem they are solving.
- Look for clues in their LinkedIn or Twitter presence if news is scarce.
- Be thorough but realistic about what is publicly available.

**OUTPUT FORMAT:**
Provide a structured summary with sections for:
- Company Basics (name, founded, HQ, team size)
- Founders & Team
- Product/Technology
- Funding History
- Traction & Growth
""",
        model_config=model_config,
        mcp_tools=[
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
        ],
        # debug=True,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 2: Market Analyst
    # ─────────────────────────────────────────────────────────────────────────
    market_analyst = OmniCoreAgent(
        name="MarketAnalyst",
        system_instruction="""
You are an expert market research analyst.

**YOUR MISSION:**
Analyze the market landscape for the provided company.

**KEY AREAS TO ANALYZE:**
1. **Market Size**: TAM (Total Addressable Market) in dollars, SAM, and CAGR.
2. **Competitors**: Who are the incumbents? Who are the disruptors? Compare funding/traction.
3. **Positioning**: How does this company differentiate itself? (Moat, Unique Value Prop).
4. **Trends**: What are the tailwinds (drivers) and headwinds (challenges)?

**OUTPUT FORMAT:**
Provide specific numbers where available. For emerging markets, provide reasoned estimates.
Structure your response with clear sections for Market Size, Competitors, Positioning, and Trends.
""",
        model_config=model_config,
        mcp_tools=[
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
        ],
        # debug=True,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 3: Financial Modeller (with Chart Tool)
    # ─────────────────────────────────────────────────────────────────────────
    modeller = OmniCoreAgent(
        name="FinancialModeller",
        system_instruction="""
You are a financial analyst specializing in startup valuation.

**YOUR TASK:**
Create a 5-year revenue projection model based on the Company and Market research.

**MODELING GUIDELINES:**
1. **Estimate Current ARR**: 
   - Seed stage: $0.1-0.5M ARR
   - Series A: $1-3M ARR
   - Series B: $5-15M ARR
   
2. **Define Three Growth Scenarios** (YoY Multipliers):
   - **Bear Case**: Conservative growth (e.g., 1.2, 1.15, 1.1, 1.1, 1.1)
   - **Base Case**: Realistic growth (e.g., 1.8, 1.6, 1.4, 1.3, 1.25)
   - **Bull Case**: Aggressive growth (e.g., 2.5, 2.0, 1.8, 1.5, 1.4)

**GENERATE THE CHART:**
Use the `generate_financial_chart` tool with these EXACT parameters:
- `company_name`: The company name (string)
- `current_arr_m`: Current ARR in millions as a number (e.g., 1.2)
- `bear_rates`: Comma-separated multipliers for bear case (e.g., "1.2,1.15,1.1,1.1,1.1")
- `base_rates`: Comma-separated multipliers for base case (e.g., "1.8,1.6,1.4,1.3,1.25")
- `bull_rates`: Comma-separated multipliers for bull case (e.g., "2.5,2.0,1.8,1.5,1.4")

**EXAMPLE TOOL CALL:**
```
generate_financial_chart(
    company_name="Acme Corp",
    current_arr_m=1.5,
    bear_rates="1.2,1.15,1.1,1.1,1.1",
    base_rates="1.8,1.6,1.4,1.3,1.25",
    bull_rates="2.5,2.0,1.8,1.5,1.4"
)
```

After generating the chart, summarize the projected Year 5 ARR for each scenario.
""",
        model_config=model_config,
        local_tools=create_chart_tool(),
        # debug=True,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 4: Risk Specialist
    # ─────────────────────────────────────────────────────────────────────────
    risk_agent = OmniCoreAgent(
        name="RiskSpecialist",
        system_instruction="""
You are a Chief Risk Officer (CRO) at a top-tier VC firm.

**YOUR OBJECTIVE:**
Conduct a thorough risk assessment of the investment opportunity.

**RISK CATEGORIES:**
1. **Market Risk**: Competition, Timing, Adoption barriers.
2. **Execution Risk**: Team gaps, Tech challenges, Sales cycles.
3. **Financial Risk**: Burn rate, Unit economics, Fundraising risk.
4. **Regulatory/Legal**: IP issues, Compliance, Data privacy.

**OUTPUT FORMAT:**
For each risk category:
- Assess **Severity**: Low / Medium / High / Critical
- Provide specific evidence or reasoning
- Suggest **Mitigation Strategies**

End with:
- **Overall Risk Score**: 1-10 scale (1=low risk, 10=do not invest)
- **Top 3 Deal-Breaker Risks**
- **Recommended Protective Terms** (board seat, milestones, etc.)
""",
        model_config=model_config,
        # debug=True,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 5: Memo Writer
    # ─────────────────────────────────────────────────────────────────────────
    memo_writer = OmniCoreAgent(
        name="MemoWriter",
        system_instruction="""
You are a General Partner writing an Investment Memo for the Investment Committee.

**MEMO STRUCTURE:**

## Executive Summary
- Company one-liner description
- **Recommendation**: BUY / HOLD / PASS
- 3-4 key highlights

## Company Overview
- Problem being solved
- Solution/Product
- Team and Founders

## Market Opportunity
- Market size (TAM/SAM)
- Why now? What's the timing catalyst?

## Financial Analysis
- Current metrics (ARR, growth rate)
- 5-year projections summary
- Unit economics assessment

## Risk Analysis
- Top 3-5 risks with severity ratings
- Overall risk score (X/10)
- Mitigation strategies

## Investment Thesis
- Core argument for investment
- Return scenarios (bear/base/bull)
- Recommended check size

Synthesize all previous findings into a compelling narrative. Be concise, persuasive, and objective.
Include specific numbers and data points throughout.
""",
        model_config=model_config,
        # debug=True,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 6: Report Generator (HTML)
    # ─────────────────────────────────────────────────────────────────────────
    report_gen = OmniCoreAgent(
        name="ReportGenerator",
        system_instruction="""
You are a Design Specialist responsible for publishing the final investment report.

**YOUR TASK:**
Convert the Investment Memo into a polished HTML document using the `generate_html_report` tool.

**TOOL PARAMETERS (REQUIRED):**
- `company_name`: The name of the company (e.g., "Agno AI")
- `report_content`: The full Investment Memo content in markdown format

**EXAMPLE TOOL CALL:**
```
generate_html_report(
    company_name="Agno AI",
    report_content="## Executive Summary\\n\\nAgno AI is..."
)
```

The tool will generate a professional McKinsey/Goldman Sachs-style HTML report with:
- Executive summary section
- Structured sections for each memo part
- Professional typography and styling
- Print-friendly layout

After calling the tool, confirm the report was saved successfully.
""",
        model_config=model_config,
        local_tools=create_report_tool(),
        # debug=True,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 7: Infographic Generator
    # ─────────────────────────────────────────────────────────────────────────
    infographic_gen = OmniCoreAgent(
        name="InfographicGenerator",
        system_instruction="""
You are a Data Visualization Expert creating investor-ready infographics.

**YOUR TASK:**
Create a professional investment summary infographic using the `generate_dashboard_infographic` tool.

**STEP 1: EXTRACT METRICS FROM THE MEMO**
Find and extract these specific values:
- Company name
- Valuation (e.g., "$50M" or "Pre-seed" if unknown)
- ARR (Annual Recurring Revenue, e.g., "$5.2M")
- Growth rate (YoY percentage, e.g., "127%")
- Market size/TAM (e.g., "$8.3B")
- Risk score (number from 1-10)
- Recommendation (BUY, HOLD, or PASS)
- Key highlights (2-4 bullet points)

**STEP 2: CALL THE TOOL WITH THESE EXACT PARAMETERS**
```
generate_dashboard_infographic(
    company_name="Agno AI",
    valuation="$50M",
    arr="$5.2M",
    growth_rate="127%",
    market_size="$8.3B",
    risk_score=4.5,
    recommendation="BUY",
    key_highlights=["Strong founding team", "3 enterprise customers", "High margins", "Clear path to profitability"]
)
```

**IMPORTANT:**
- `valuation`: Format as currency string (e.g., "$50M", "$1.2B") or "N/A" if unknown
- `arr`: Format as currency string (e.g., "$5.2M")
- `growth_rate`: Format as percentage string (e.g., "127%", "85%")
- `market_size`: Format as currency string (e.g., "$8.3B")
- `risk_score`: Must be a NUMBER between 1 and 10 (not a string)
- `recommendation`: Must be one of "BUY", "HOLD", or "PASS"
- `key_highlights`: List of 2-4 short bullet point strings

After calling the tool, confirm the infographic was generated successfully.
""",
        model_config=model_config,
        local_tools=create_infographic_tool(),
        # debug=True,
    )

    return (
        researcher,
        market_analyst,
        modeller,
        risk_agent,
        memo_writer,
        report_gen,
        infographic_gen,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main Workflow
# ─────────────────────────────────────────────────────────────────────────────


async def run_due_diligence(
    company_name: str,
    model: str = "gpt-4o",
    provider: str = "openai",
    verbose: bool = True,
):
    """Execute the full due diligence workflow."""

    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        print(
            f"{Colors.RED}Error: TAVILY_API_KEY environment variable not set.{Colors.RESET}"
        )
        print(
            f"{Colors.DIM}Set it in your .env file or export it in your shell.{Colors.RESET}"
        )
        return None

    start_time = time.time()
    outputs = {}
    total_stages = 7

    # Initialize Agents
    if verbose:
        print(
            f"{Colors.DIM}Initializing agents using {provider}/{model}...{Colors.RESET}"
        )

    researcher, market, modeller, risk, memo, report, infographic = create_agents(
        tavily_key, model, provider
    )

    # Connect MCP servers
    await researcher.connect_mcp_servers()
    await market.connect_mcp_servers()

    try:
        # ─────────────────────────────────────────────────────────────────
        # Stage 1: Company Research
        # ─────────────────────────────────────────────────────────────────
        if verbose:
            print_stage(1, total_stages, f"Researching {company_name}...")

        res1 = await researcher.run(f"Research this company thoroughly: {company_name}")
        info_data = res1["response"]
        outputs["research"] = info_data

        if verbose:
            print_stage(1, total_stages, "Company Research", "complete")

        # ─────────────────────────────────────────────────────────────────
        # Stage 2: Market Analysis
        # ─────────────────────────────────────────────────────────────────
        if verbose:
            print_stage(2, total_stages, "Analyzing market landscape...")

        res2 = await market.run(f"""
COMPANY INFO:
{info_data}

TASK: Analyze the market size, competitors, and positioning for this company.
""")
        market_data = res2["response"]
        outputs["market"] = market_data

        if verbose:
            print_stage(2, total_stages, "Market Analysis", "complete")

        # ─────────────────────────────────────────────────────────────────
        # Stage 3: Financial Modeling
        # ─────────────────────────────────────────────────────────────────
        if verbose:
            print_stage(
                3, total_stages, "Building financial model & generating chart..."
            )

        res3 = await modeller.run(f"""
COMPANY: {company_name}
COMPANY INFO: {info_data}
MARKET ANALYSIS: {market_data}

TASK: Create a 5-year financial projection model and generate the revenue chart.
Remember to use the generate_financial_chart tool with bear_rates, base_rates, and bull_rates.
""")
        finance_data = res3["response"]
        outputs["finance"] = finance_data

        if verbose:
            print_stage(3, total_stages, "Financial Modeling", "complete")

        # ─────────────────────────────────────────────────────────────────
        # Stage 4: Risk Assessment
        # ─────────────────────────────────────────────────────────────────
        if verbose:
            print_stage(4, total_stages, "Assessing investment risks...")

        res4 = await risk.run(f"""
Conduct a comprehensive risk assessment for {company_name}:

COMPANY INFO:
{info_data}

FINANCIAL MODEL:
{finance_data}

Provide severity ratings and an overall risk score (1-10).
""")
        risk_data = res4["response"]
        outputs["risk"] = risk_data

        if verbose:
            print_stage(4, total_stages, "Risk Assessment", "complete")

        # ─────────────────────────────────────────────────────────────────
        # Stage 5: Investment Memo
        # ─────────────────────────────────────────────────────────────────
        if verbose:
            print_stage(5, total_stages, "Writing investment memo...")

        res5 = await memo.run(f"""
Write a comprehensive Investment Memo for {company_name}:

COMPANY RESEARCH:
{info_data}

MARKET ANALYSIS:
{market_data}

FINANCIAL MODEL:
{finance_data}

RISK ASSESSMENT:
{risk_data}
""")
        memo_data = res5["response"]
        outputs["memo"] = memo_data

        if verbose:
            print_stage(5, total_stages, "Investment Memo", "complete")
            print(f"\n{Colors.DIM}─── Memo Preview ───{Colors.RESET}")
            preview = memo_data[:600] + "..." if len(memo_data) > 600 else memo_data
            print(f"{Colors.DIM}{preview}{Colors.RESET}\n")

        # ─────────────────────────────────────────────────────────────────
        # Stage 6: HTML Report
        # ─────────────────────────────────────────────────────────────────
        if verbose:
            print_stage(6, total_stages, "Generating HTML report...")

        await report.run(f"""
Generate a professional HTML report for the {company_name} investment analysis.

Use the generate_html_report tool with:
- company_name: "{company_name}"
- report_content: The full memo below

INVESTMENT MEMO:
{memo_data}
""")

        if verbose:
            print_stage(6, total_stages, "HTML Report", "complete")

        # ─────────────────────────────────────────────────────────────────
        # Stage 7: Infographic
        # ─────────────────────────────────────────────────────────────────
        if verbose:
            print_stage(7, total_stages, "Creating infographic...")

        await infographic.run(f"""
Create a professional investment infographic for {company_name}.

Extract the key metrics from this memo and use the generate_dashboard_infographic tool:

INVESTMENT MEMO:
{memo_data}

Remember to pass each parameter individually: company_name, valuation, arr, growth_rate, market_size, risk_score (as a number), recommendation, and key_highlights (as a list).
""")

        if verbose:
            print_stage(7, total_stages, "Infographic", "complete")

    finally:
        # Cleanup MCP connections
        await researcher.cleanup_mcp_servers()
        await market.cleanup_mcp_servers()

    elapsed = time.time() - start_time

    if verbose:
        print_summary(company_name, outputs, elapsed)
        print(
            f"{Colors.DIM}Settings: {provider}/{model} | Completed: {datetime.now().strftime('%H:%M:%S')}{Colors.RESET}\n"
        )

    return outputs


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="due_diligence_workflow",
        description="🔍 AI Due Diligence Workflow - Analyze startups for investment potential",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python due_diligence_workflow.py "Agno AI"
  python due_diligence_workflow.py --company "OpenAI"
  python due_diligence_workflow.py -c "Anthropic" --quiet

Environment Variables:
  TAVILY_API_KEY    API key for Tavily search (required)
  LLM_API_KEY       API key for the LLM provider (required)
        """,
    )

    parser.add_argument("company", nargs="?", help="Name of the company to analyze")

    parser.add_argument(
        "-c",
        "--company",
        dest="company_flag",
        help="Name of the company to analyze (alternative to positional argument)",
    )

    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress progress output (only show final summary)",
    )

    parser.add_argument(
        "-m", "--model", default="gpt-4.1", help="LLM model to use (default: gpt-4.1)"
    )

    parser.add_argument(
        "-p", "--provider", default="openai", help="LLM provider (default: openai)"
    )

    parser.add_argument(
        "--version", action="version", version="%(prog)s 1.0.0 (OmniCoreAgent)"
    )

    return parser.parse_args()


async def main():
    """Main entry point."""
    load_dotenv()

    args = parse_args()

    # Determine company name from args
    company = args.company_flag or args.company

    # Interactive mode if no company provided
    if not company:
        print_header()
        print(f"{Colors.BOLD}Enter the company name to analyze:{Colors.RESET}")
        company = input(f"{Colors.CYAN}> {Colors.RESET}").strip()

        if not company:
            print(f"{Colors.RED}Error: No company name provided.{Colors.RESET}")
            sys.exit(1)
    else:
        print_header()

    print(
        f"\n{Colors.BOLD}Analyzing:{Colors.RESET} {Colors.CYAN}{company}{Colors.RESET}"
    )
    print(
        f"{Colors.BOLD}Model:{Colors.RESET}     {Colors.DIM}{args.provider}/{args.model}{Colors.RESET}"
    )
    print(
        f"{Colors.DIM}This may take 3-5 minutes depending on the company...{Colors.RESET}"
    )

    await run_due_diligence(
        company, model=args.model, provider=args.provider, verbose=not args.quiet
    )


if __name__ == "__main__":
    asyncio.run(main())
