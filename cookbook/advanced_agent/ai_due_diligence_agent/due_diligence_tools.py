"""
Premium Validation Tools for Due Diligence Workflow.

This module provides high-quality, local implementation of the workflow tools.
It creates specific tool registries for each agent to ensure strict Separation of Concerns.
"""

import os
import logging
from datetime import datetime
from omnicoreagent import ToolRegistry

# Try importing matplotlib
try:
    import matplotlib.pyplot as plt
    import matplotlib

    matplotlib.use("Agg")  # Headless mode
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

# Try importing OpenAI
try:
    from openai import OpenAI

    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

logger = logging.getLogger("DueDiligenceTools")


def ensure_string(text: str | list) -> str:
    """Helper to ensure input is a string."""
    if isinstance(text, list):
        return " ".join(str(x) for x in text)
    return str(text)


def get_output_dir() -> str:
    """Returns the absolute path to the outputs directory."""
    output_dir = os.path.join(os.getcwd(), "outputs")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def create_chart_tool() -> ToolRegistry:
    """Creates a registry containing ONLY the financial chart tool."""
    registry = ToolRegistry()

    @registry.register_tool(
        name="generate_financial_chart",
        description="""
        Generate a professional revenue projection chart with bear/base/bull scenarios.

        **When to use:**
        Use this tool when you need to visualize financial projections for an investment 
        analysis. The chart shows three scenarios (pessimistic, expected, optimistic) to 
        help investors understand the range of possible outcomes.

        **What it produces:**
        A PNG image file showing:
        - Three projection lines (Bear: red, Base: blue, Bull: green)
        - Shaded confidence region between bear and bull cases
        - Value annotations on the base case line
        - Professional styling suitable for investor presentations

        **How to use:**
        1. Provide the company name for the chart title
        2. Set the current ARR (Annual Recurring Revenue) in millions
        3. Provide growth multipliers for each scenario (e.g., 1.5 means 50% growth)
        4. The number of years projected equals the number of rates provided

        **Example:**
        For a company with $1.2M ARR projecting 5 years:
        - bear_rates: "1.1,1.1,1.1,1.1,1.1" (10% growth each year)
        - base_rates: "1.5,1.4,1.3,1.25,1.2" (decreasing growth)
        - bull_rates: "2.0,1.8,1.6,1.4,1.3" (aggressive early growth)
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "Name of the company. This will appear as the chart title.",
                },
                "current_arr_m": {
                    "type": "number",
                    "description": "Current Annual Recurring Revenue in millions of dollars. Example: 1.2 means $1.2M.",
                },
                "bear_rates": {
                    "type": "string",
                    "description": "Comma-separated YoY growth multipliers for the pessimistic (bear) case. Example: '1.1,1.15,1.2' means 10%, 15%, 20% growth.",
                },
                "base_rates": {
                    "type": "string",
                    "description": "Comma-separated YoY growth multipliers for the expected (base) case. Example: '1.5,1.4,1.3' means 50%, 40%, 30% growth.",
                },
                "bull_rates": {
                    "type": "string",
                    "description": "Comma-separated YoY growth multipliers for the optimistic (bull) case. Example: '2.0,1.8,1.5' means 100%, 80%, 50% growth.",
                },
            },
            "required": [
                "company_name",
                "current_arr_m",
                "bear_rates",
                "base_rates",
                "bull_rates",
            ],
            "additionalProperties": False,
        },
    )
    def generate_financial_chart(
        company_name: str,
        current_arr_m: float,
        bear_rates: str | list,
        base_rates: str | list,
        bull_rates: str | list,
    ) -> dict:
        """Internal implementation - see decorator for full documentation."""
        if not HAS_MATPLOTLIB:
            return {"status": "error", "message": "Matplotlib is not installed."}

        def parse_rates(rates_input: str | list) -> list[float]:
            """Parse rates from string or list."""
            if isinstance(rates_input, list):
                return [float(x) for x in rates_input]
            s = str(rates_input).strip()
            if "," in s:
                return [float(x.strip()) for x in s.split(",")]
            return [float(x.strip()) for x in s.split()]

        def project_arr(start: float, rates: list[float]) -> list[float]:
            """Project ARR values over time."""
            arr = [start]
            for rate in rates:
                arr.append(arr[-1] * rate)
            return arr

        try:
            company_name = ensure_string(company_name)
            current = float(current_arr_m)

            # Parse all growth rate scenarios
            bear = parse_rates(bear_rates)
            base = parse_rates(base_rates)
            bull = parse_rates(bull_rates)

            # Generate year labels
            start_year = datetime.now().year
            years = list(range(start_year, start_year + len(base) + 1))

            # Calculate projections for each scenario
            bear_arr = project_arr(current, bear)
            base_arr = project_arr(current, base)
            bull_arr = project_arr(current, bull)

            # Create professional chart
            plt.style.use("seaborn-v0_8-whitegrid")
            fig, ax = plt.subplots(figsize=(10, 6))

            # Plot all three scenarios
            ax.plot(
                years,
                bear_arr,
                "o-",
                color="#dc2626",
                linewidth=2,
                markersize=8,
                label="Bear Case",
            )
            ax.plot(
                years,
                base_arr,
                "s-",
                color="#1a365d",
                linewidth=3,
                markersize=10,
                label="Base Case",
            )
            ax.plot(
                years,
                bull_arr,
                "^-",
                color="#16a34a",
                linewidth=2,
                markersize=8,
                label="Bull Case",
            )

            # Add shaded region between bear and bull
            ax.fill_between(years, bear_arr, bull_arr, alpha=0.1, color="#1a365d")

            # Styling
            ax.set_title(
                f"{company_name} - Revenue Projection Analysis",
                fontsize=16,
                fontweight="bold",
                color="#1a365d",
            )
            ax.set_xlabel("Year", fontsize=12)
            ax.set_ylabel("ARR ($ Millions)", fontsize=12)
            ax.legend(loc="upper left", fontsize=11)

            # Add value annotations for base case
            for x, y in zip(years, base_arr):
                ax.annotate(
                    f"${y:.1f}M",
                    (x, y),
                    textcoords="offset points",
                    xytext=(0, 10),
                    ha="center",
                    fontsize=9,
                    color="#1a365d",
                )

            ax.grid(True, linestyle="--", alpha=0.7)
            plt.tight_layout()

            # Save chart
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"revenue_chart_{company_name.replace(' ', '_')}_{timestamp}.png"
            filepath = os.path.join(get_output_dir(), filename)
            plt.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="white")
            plt.close()

            return {
                "status": "success",
                "message": f"Chart generated successfully: {filepath}",
                "data": {
                    "filepath": filepath,
                    "summary": {
                        f"year_{len(base)}_bear": f"${bear_arr[-1]:.1f}M",
                        f"year_{len(base)}_base": f"${base_arr[-1]:.1f}M",
                        f"year_{len(base)}_bull": f"${bull_arr[-1]:.1f}M",
                    },
                },
            }
        except Exception as e:
            logger.error(f"Error generating chart: {e}")
            return {"status": "error", "message": f"Error plotting chart: {str(e)}"}

    return registry


def create_infographic_tool() -> ToolRegistry:
    """Creates a registry containing ONLY the infographic dashboard tool."""
    registry = ToolRegistry()

    @registry.register_tool(
        name="generate_dashboard_infographic",
        description="""
        Generate a professional investment infographic (Goldman Sachs-style one-pager).

        **When to use:**
        Use this tool to create a visually stunning, investor-ready summary of a company's 
        key metrics. This is ideal for executive presentations, pitch decks, or investment 
        committee materials. The infographic will display all metrics with 100% accuracy.

        **What it produces:**
        A PNG image file (16:9 aspect ratio) containing:
        - Dark blue header with company name and date
        - Four metric cards: Valuation, ARR, YoY Growth, Market Size (TAM)
        - Color-coded risk gauge (green to red scale, 1-10)
        - Investment recommendation badge (BUY=green, HOLD=orange, PASS=red)
        - Up to 4 key highlights with bullet points
        - Professional footer with confidentiality notice

        **How to use:**
        1. Gather the key financial metrics from your analysis
        2. Format values with currency symbols and units (e.g., "$50M", "127%", "$8.3B")
        3. Set risk_score between 1 (low risk) and 10 (high risk)
        4. Choose recommendation: "BUY", "HOLD", or "PASS"
        5. Optionally provide 2-4 key highlights as bullet points

        **Example:**
        - valuation: "$50M"
        - arr: "$5.2M"
        - growth_rate: "127%"
        - market_size: "$8.3B"
        - risk_score: 4.5
        - recommendation: "BUY"
        - key_highlights: ["Strong founding team", "3 enterprise customers", "High margins"]
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "Name of the company. Displayed prominently in the header.",
                },
                "valuation": {
                    "type": "string",
                    "description": "Current company valuation. Include currency symbol and unit (e.g., '$50M', '$1.2B').",
                },
                "arr": {
                    "type": "string",
                    "description": "Annual Recurring Revenue. Include currency symbol and unit (e.g., '$5.2M').",
                },
                "growth_rate": {
                    "type": "string",
                    "description": "Year-over-year revenue growth rate. Include percentage symbol (e.g., '127%', '85%').",
                },
                "market_size": {
                    "type": "string",
                    "description": "Total Addressable Market (TAM). Include currency and unit (e.g., '$8.3B', '$50B').",
                },
                "risk_score": {
                    "type": "number",
                    "description": "Investment risk score from 1 to 10. Lower is better (1=low risk, 10=high risk).",
                },
                "recommendation": {
                    "type": "string",
                    "enum": [
                        "BUY",
                        "HOLD",
                        "PASS",
                        "STRONG BUY",
                        "INVEST",
                        "MONITOR",
                        "NEUTRAL",
                    ],
                    "description": "Investment recommendation. BUY/INVEST=green badge, HOLD/MONITOR=orange, PASS=red.",
                },
                "key_highlights": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of 2-4 key highlights or bullet points about the opportunity.",
                },
            },
            "required": [
                "company_name",
                "valuation",
                "arr",
                "growth_rate",
                "market_size",
                "risk_score",
                "recommendation",
            ],
            "additionalProperties": False,
        },
    )
    def generate_dashboard_infographic(
        company_name: str,
        valuation: str,
        arr: str,
        growth_rate: str,
        market_size: str,
        risk_score: float,
        recommendation: str,
        key_highlights: str | list | None = None,
    ) -> dict:
        """Internal implementation - see decorator for full documentation."""
        if not HAS_MATPLOTLIB:
            return {"status": "error", "message": "Matplotlib is not installed."}

        try:
            import numpy as np
            from matplotlib.patches import FancyBboxPatch, Circle, Wedge
            from matplotlib.collections import PatchCollection
            import matplotlib.patheffects as path_effects

            # Ensure clean string values
            company_name = ensure_string(company_name)
            valuation = ensure_string(valuation)
            arr = ensure_string(arr)
            growth_rate = ensure_string(growth_rate)
            market_size = ensure_string(market_size)
            recommendation = ensure_string(recommendation).upper().strip()

            # Normalize risk score
            try:
                risk_score = float(risk_score)
                risk_score = max(1, min(10, risk_score))
            except:
                risk_score = 5.0

            # Parse highlights
            highlights = []
            if key_highlights:
                if isinstance(key_highlights, list):
                    highlights = [ensure_string(h) for h in key_highlights[:4]]
                else:
                    # Try to parse as comma or newline separated
                    h_str = ensure_string(key_highlights)
                    if "\n" in h_str:
                        highlights = [
                            h.strip() for h in h_str.split("\n") if h.strip()
                        ][:4]
                    elif "," in h_str:
                        highlights = [h.strip() for h in h_str.split(",") if h.strip()][
                            :4
                        ]
                    else:
                        highlights = [h_str]

            # Color scheme
            DARK_BLUE = "#1a365d"
            GOLD = "#d4af37"
            LIGHT_GRAY = "#f8f9fa"
            MEDIUM_GRAY = "#6c757d"
            WHITE = "#ffffff"
            GREEN = "#198754"
            RED = "#dc3545"
            ORANGE = "#fd7e14"

            # Create figure with professional dimensions (16:9 aspect for presentations)
            fig = plt.figure(figsize=(16, 9), facecolor=WHITE)
            fig.subplots_adjust(left=0.05, right=0.95, top=0.92, bottom=0.08)

            # Main axis for the layout
            ax = fig.add_axes([0, 0, 1, 1])
            ax.set_xlim(0, 16)
            ax.set_ylim(0, 9)
            ax.set_aspect("equal")
            ax.axis("off")

            # === HEADER SECTION ===
            # Background header bar
            header_bg = FancyBboxPatch(
                (0, 7.5),
                16,
                1.5,
                boxstyle="square,pad=0",
                facecolor=DARK_BLUE,
                edgecolor="none",
            )
            ax.add_patch(header_bg)

            # Company name
            ax.text(
                0.5,
                8.25,
                company_name.upper(),
                fontsize=32,
                fontweight="bold",
                color=WHITE,
                va="center",
                ha="left",
                path_effects=[
                    path_effects.withStroke(linewidth=1, foreground=DARK_BLUE)
                ],
            )

            # Subtitle
            ax.text(
                0.5,
                7.75,
                "INVESTMENT DUE DILIGENCE REPORT",
                fontsize=12,
                color=GOLD,
                va="center",
                ha="left",
                style="italic",
            )

            # Date on right
            ax.text(
                15.5,
                8.0,
                datetime.now().strftime("%B %d, %Y"),
                fontsize=11,
                color=WHITE,
                va="center",
                ha="right",
            )

            # === METRIC CARDS ROW ===
            def draw_metric_card(x, y, w, h, label, value, accent_color=GOLD):
                """Draw a professional metric card."""
                # Card background
                card = FancyBboxPatch(
                    (x, y),
                    w,
                    h,
                    boxstyle="round,pad=0.03,rounding_size=0.1",
                    facecolor=WHITE,
                    edgecolor="#e0e0e0",
                    linewidth=1,
                )
                ax.add_patch(card)
                # Top accent line
                ax.plot(
                    [x + 0.1, x + w - 0.1],
                    [y + h - 0.1, y + h - 0.1],
                    color=accent_color,
                    linewidth=4,
                    solid_capstyle="round",
                )
                # Label
                ax.text(
                    x + w / 2,
                    y + h - 0.45,
                    label.upper(),
                    fontsize=10,
                    color=MEDIUM_GRAY,
                    va="center",
                    ha="center",
                    fontweight="medium",
                )
                # Value
                ax.text(
                    x + w / 2,
                    y + h / 2 - 0.15,
                    value,
                    fontsize=24,
                    color=DARK_BLUE,
                    va="center",
                    ha="center",
                    fontweight="bold",
                )

            # Draw 4 metric cards
            card_y = 5.5
            card_h = 1.8
            card_w = 3.5
            gap = 0.4
            start_x = 0.5

            draw_metric_card(start_x, card_y, card_w, card_h, "Valuation", valuation)
            draw_metric_card(
                start_x + card_w + gap, card_y, card_w, card_h, "ARR", arr, GREEN
            )
            draw_metric_card(
                start_x + 2 * (card_w + gap),
                card_y,
                card_w,
                card_h,
                "YoY Growth",
                growth_rate,
                GREEN,
            )
            draw_metric_card(
                start_x + 3 * (card_w + gap),
                card_y,
                card_w,
                card_h,
                "Market Size (TAM)",
                market_size,
            )

            # === RISK GAUGE ===
            gauge_center_x = 3.5
            gauge_center_y = 3.2
            gauge_radius = 1.8

            # Background arc
            theta_start = 180
            for i in range(10):
                segment_color = plt.cm.RdYlGn_r(i / 9)
                wedge = Wedge(
                    (gauge_center_x, gauge_center_y),
                    gauge_radius,
                    theta_start + i * 18,
                    theta_start + (i + 1) * 18,
                    width=0.4,
                    facecolor=segment_color,
                    edgecolor=WHITE,
                    linewidth=1,
                )
                ax.add_patch(wedge)

            # Needle
            needle_angle = 180 + (risk_score - 1) * 18
            needle_rad = np.radians(needle_angle)
            needle_len = gauge_radius - 0.5
            ax.arrow(
                gauge_center_x,
                gauge_center_y,
                needle_len * np.cos(needle_rad),
                needle_len * np.sin(needle_rad),
                head_width=0.15,
                head_length=0.1,
                fc=DARK_BLUE,
                ec=DARK_BLUE,
                linewidth=2,
            )

            # Center circle
            center_circle = Circle(
                (gauge_center_x, gauge_center_y),
                0.2,
                facecolor=DARK_BLUE,
                edgecolor=WHITE,
                linewidth=2,
            )
            ax.add_patch(center_circle)

            # Risk score label
            ax.text(
                gauge_center_x,
                gauge_center_y - 1.0,
                "RISK SCORE",
                fontsize=10,
                color=MEDIUM_GRAY,
                va="center",
                ha="center",
                fontweight="medium",
            )
            ax.text(
                gauge_center_x,
                gauge_center_y - 1.4,
                f"{risk_score:.1f}/10",
                fontsize=20,
                color=DARK_BLUE,
                va="center",
                ha="center",
                fontweight="bold",
            )

            # Risk labels
            ax.text(
                gauge_center_x - gauge_radius - 0.3,
                gauge_center_y - 0.2,
                "LOW",
                fontsize=8,
                color=GREEN,
                va="center",
                ha="center",
                fontweight="bold",
            )
            ax.text(
                gauge_center_x + gauge_radius + 0.3,
                gauge_center_y - 0.2,
                "HIGH",
                fontsize=8,
                color=RED,
                va="center",
                ha="center",
                fontweight="bold",
            )

            # === RECOMMENDATION BADGE ===
            rec_x = 12.5
            rec_y = 2.5

            # Determine badge color
            rec_upper = recommendation.upper()
            if rec_upper in ["BUY", "STRONG BUY", "INVEST"]:
                badge_color = GREEN
            elif rec_upper in ["HOLD", "NEUTRAL", "MONITOR"]:
                badge_color = ORANGE
            else:
                badge_color = RED

            # Badge background
            badge = FancyBboxPatch(
                (rec_x - 1.8, rec_y - 0.8),
                3.6,
                2.2,
                boxstyle="round,pad=0.05,rounding_size=0.2",
                facecolor=badge_color,
                edgecolor="none",
            )
            ax.add_patch(badge)

            # Badge text
            ax.text(
                rec_x,
                rec_y + 0.45,
                "RECOMMENDATION",
                fontsize=10,
                color=WHITE,
                va="center",
                ha="center",
                alpha=0.9,
            )
            ax.text(
                rec_x,
                rec_y - 0.15,
                recommendation.upper(),
                fontsize=28,
                color=WHITE,
                va="center",
                ha="center",
                fontweight="bold",
            )

            # === KEY HIGHLIGHTS SECTION ===
            if highlights:
                hl_x = 6.5
                hl_y = 4.0

                ax.text(
                    hl_x,
                    hl_y,
                    "KEY HIGHLIGHTS",
                    fontsize=11,
                    color=DARK_BLUE,
                    va="center",
                    ha="left",
                    fontweight="bold",
                )

                # Gold underline
                ax.plot(
                    [hl_x, hl_x + 3],
                    [hl_y - 0.15, hl_y - 0.15],
                    color=GOLD,
                    linewidth=2,
                )

                for i, highlight in enumerate(highlights):
                    bullet_y = hl_y - 0.5 - (i * 0.5)
                    # Bullet point
                    bullet = Circle(
                        (hl_x + 0.1, bullet_y), 0.08, facecolor=GOLD, edgecolor="none"
                    )
                    ax.add_patch(bullet)
                    # Text (truncate if too long)
                    display_text = (
                        highlight[:60] + "..." if len(highlight) > 60 else highlight
                    )
                    ax.text(
                        hl_x + 0.35,
                        bullet_y,
                        display_text,
                        fontsize=10,
                        color=DARK_BLUE,
                        va="center",
                        ha="left",
                    )

            # === FOOTER ===
            ax.plot([0.5, 15.5], [0.6, 0.6], color="#e0e0e0", linewidth=1)
            ax.text(
                8,
                0.3,
                "CONFIDENTIAL - FOR INVESTMENT PURPOSES ONLY",
                fontsize=9,
                color=MEDIUM_GRAY,
                va="center",
                ha="center",
                style="italic",
            )

            # Save
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"infographic_{company_name.replace(' ', '_')}_{timestamp}.png"
            filepath = os.path.join(get_output_dir(), filename)
            plt.savefig(
                filepath,
                dpi=150,
                bbox_inches="tight",
                facecolor=WHITE,
                edgecolor="none",
                pad_inches=0.2,
            )
            plt.close()

            logger.info(f"Professional infographic generated: {filepath}")
            return {
                "status": "success",
                "message": f"Professional infographic saved: {filepath}",
                "data": {
                    "filepath": filepath,
                    "generator": "matplotlib-professional",
                    "metrics": {
                        "company": company_name,
                        "valuation": valuation,
                        "arr": arr,
                        "growth": growth_rate,
                        "market_size": market_size,
                        "risk_score": risk_score,
                        "recommendation": recommendation,
                    },
                },
            }
        except Exception as e:
            logger.error(f"Error creating infographic: {e}")
            return {
                "status": "error",
                "message": f"Error creating infographic: {str(e)}",
            }

    return registry


def create_report_tool() -> ToolRegistry:
    """Creates a registry containing ONLY the HTML report generation tool."""
    registry = ToolRegistry()

    @registry.register_tool(
        name="generate_html_report",
        description="""
        Generate a professional HTML investment report styled like McKinsey/Goldman Sachs.

        **When to use:**
        Use this tool as the final step to compile all your due diligence findings into a 
        polished, investor-ready HTML document. This tool does NOT use an LLM - it directly 
        formats your content into a professional template. No external API calls required.

        **What it produces:**
        An HTML file with:
        - Professional header with company name and date
        - Executive summary section with gold accent styling
        - Optional structured sections (if provided)
        - Full markdown support (headers, bold, italic, lists, tables, code blocks)
        - Print-friendly layout for PDF export
        - Confidentiality footer

        **Markdown support:**
        The tool converts markdown to HTML, supporting:
        - Headers (# to ######)
        - **Bold** and *italic* text
        - Bullet lists (- or *)
        - Numbered lists (1. 2. 3.)
        - Code blocks (```)
        - Links [text](url)
        - Tables

        **How to use:**
        1. Compile your due diligence findings into markdown format
        2. For structured reports, pass a sections dict with section titles as keys
        3. The report will be saved as an HTML file that can be opened in any browser

        **Example with sections:**
        {
            "sections": {
                "Company Overview": "Founded in 2023, the company is...",
                "Market Opportunity": "The TAM is estimated at $50B...",
                "Financial Analysis": "Current ARR of $5.2M with 127% growth...",
                "Risk Assessment": "Key risks include competition and...",
                "Recommendation": "We recommend a BUY position based on..."
            }
        }
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "company_name": {
                    "type": "string",
                    "description": "Name of the company. Displayed as the report title and in the header.",
                },
                "report_content": {
                    "type": "string",
                    "description": "Main report content in markdown or plain text. Used for the executive summary and body if no sections provided.",
                },
                "sections": {
                    "type": "object",
                    "description": "Optional dict of section titles to section content. Each key becomes a section header, each value is the section body (supports markdown).",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["company_name", "report_content"],
            "additionalProperties": False,
        },
    )
    def generate_html_report(
        company_name: str,
        report_content: str | list,
        sections: dict | str | None = None,
    ) -> dict:
        """Internal implementation - see decorator for full documentation."""

        # Comprehensive markdown to HTML conversion
        def to_html(text: str) -> str:
            """Convert markdown to HTML with full syntax support."""
            import re

            # First, try the markdown library for best results
            try:
                import markdown as md_lib

                return md_lib.markdown(
                    text,
                    extensions=[
                        "tables",
                        "fenced_code",
                        "nl2br",
                        "sane_lists",
                        "smarty",
                    ],
                )
            except ImportError:
                pass

            # 0. Handle Code Blocks (must be first)
            def replace_code_block(match):
                lang = match.group(1) or ""
                code = match.group(2).strip()
                return f'<pre><code class="language-{lang}">{code}</code></pre>'

            text = re.sub(
                r"```(\w*)\n(.*?)```", replace_code_block, text, flags=re.DOTALL
            )

            # Fallback implementation: Process line by line for block elements, then inline
            lines = text.split("\n")
            processed_lines = []
            in_list = False
            list_type = None  # 'ul' or 'ol'

            for line in lines:
                original_line = line
                stripped = line.strip()

                # 1. Handle Headers
                header_match = re.match(r"^(\s*)(#{1,6})\s+(.+)$", line)
                if header_match:
                    if in_list:
                        processed_lines.append(f"</{list_type}>")
                        in_list = False
                    level = len(header_match.group(2))
                    content = header_match.group(3)
                    processed_lines.append(f"<h{level}>{content}</h{level}>")
                    continue

                # 2. Handle Horizontal Rules
                if re.match(r"^\s*([-*]){3,}\s*$", line):
                    if in_list:
                        processed_lines.append(f"</{list_type}>")
                        in_list = False
                    processed_lines.append("<hr>")
                    continue

                # 3. Handle Blockquotes
                if line.startswith("> "):
                    if in_list:
                        processed_lines.append(f"</{list_type}>")
                        in_list = False
                    processed_lines.append(
                        f"<blockquote>{line[2:].strip()}</blockquote>"
                    )
                    continue

                # 4. Handle Lists
                ul_match = re.match(r"^\s*[\-\*]\s+(.+)$", line)
                ol_match = re.match(r"^\s*\d+\.\s+(.+)$", line)

                if ul_match or ol_match:
                    current_type = "ul" if ul_match else "ol"
                    content = ul_match.group(1) if ul_match else ol_match.group(1)

                    if not in_list:
                        processed_lines.append(f"<{current_type}>")
                        in_list = True
                        list_type = current_type
                    elif list_type != current_type:
                        processed_lines.append(f"</{list_type}>")
                        processed_lines.append(f"<{current_type}>")
                        list_type = current_type

                    processed_lines.append(f"<li>{content}</li>")
                    continue

                # If we were in a list and this line isn't a list item, close it
                if in_list and stripped != "":
                    processed_lines.append(f"</{list_type}>")
                    in_list = False

                # 4. Handle Paragraphs / Regular text
                if stripped == "":
                    processed_lines.append("")
                else:
                    processed_lines.append(line)

            if in_list:
                processed_lines.append(f"</{list_type}>")

            # Rejoin and process inline elements
            html = "\n".join(processed_lines)

            # Bold/Italic (order matters)
            html = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", html)
            html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
            html = re.sub(r"__(.+?)__", r"<strong>\1</strong>", html)
            html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)
            html = re.sub(r"_(.+?)_", r"<em>\1</em>", html)

            # Links and Code
            html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', html)
            html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)

            # Final Paragraph Wrapping for non-HTML blocks
            paragraphs = html.split("\n\n")
            final_html = []
            for p in paragraphs:
                p = p.strip()
                if not p:
                    continue
                if (
                    p.startswith("<h")
                    or p.startswith("<ul")
                    or p.startswith("<ol")
                    or p.startswith("<hr")
                    or p.startswith("<pre")
                    or p.startswith("<blockquote")
                ):
                    final_html.append(p)
                else:
                    # Convert internal newlines to <br>
                    p = p.replace("\n", "<br>\n")
                    final_html.append(f"<p>{p}</p>")

            return "\n".join(final_html)

        try:
            company_name = ensure_string(company_name)
            content = ensure_string(report_content)
            current_date = datetime.now().strftime("%B %d, %Y")

            # Build sections HTML if provided
            sections_html = ""
            if sections:
                import json
                import ast

                if isinstance(sections, str):
                    try:
                        sections = json.loads(sections)
                    except json.JSONDecodeError:
                        try:
                            sections = ast.literal_eval(sections)
                        except:
                            sections = {}

                if isinstance(sections, dict):
                    for title, sec_content in sections.items():
                        sections_html += f"""
                        <section class="report-section">
                            <h2>{title}</h2>
                            {to_html(ensure_string(sec_content))}
                        </section>
                        """

            # Professional HTML template
            template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Investment Report - {company_name}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Georgia', 'Times New Roman', serif;
            line-height: 1.7;
            color: #333;
            background: #fff;
            max-width: 900px;
            margin: 0 auto;
            padding: 40px 60px;
        }}
        
        .header {{
            border-bottom: 3px solid #d4af37;
            padding-bottom: 30px;
            margin-bottom: 40px;
        }}
        
        .header-meta {{
            color: #666;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 2px;
            margin-bottom: 10px;
        }}
        
        h1 {{
            color: #1a365d;
            font-size: 2.5em;
            font-weight: 700;
            margin-bottom: 10px;
        }}
        
        .date {{
            color: #888;
            font-style: italic;
        }}
        
        h2 {{
            color: #1a365d;
            font-size: 1.4em;
            margin-top: 35px;
            margin-bottom: 15px;
            padding-bottom: 8px;
            border-bottom: 1px solid #e0e0e0;
        }}
        
        h3 {{
            color: #2d4a6f;
            font-size: 1.1em;
            margin-top: 25px;
            margin-bottom: 10px;
        }}
        
        p {{
            margin-bottom: 15px;
            text-align: justify;
        }}
        
        .report-section {{
            margin-bottom: 30px;
        }}
        
        .executive-summary {{
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
            border-left: 4px solid #d4af37;
            padding: 25px 30px;
            margin: 30px 0;
        }}
        
        .executive-summary h2 {{
            margin-top: 0;
            border-bottom: none;
            color: #1a365d;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }}
        
        th, td {{
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        
        th {{
            background: #1a365d;
            color: white;
            font-weight: 600;
        }}
        
        tr:nth-child(even) {{
            background: #f8f9fa;
        }}
        
        .recommendation {{
            background: #1a365d;
            color: white;
            padding: 25px 30px;
            margin: 30px 0;
            text-align: center;
        }}
        
        .recommendation h2 {{
            color: #d4af37;
            border: none;
            margin: 0 0 10px 0;
        }}
        
        .footer {{
            margin-top: 50px;
            padding-top: 20px;
            border-top: 2px solid #1a365d;
            color: #666;
            font-size: 0.85em;
            text-align: center;
        }}
        
        ul, ol {{
            margin: 15px 0 15px 25px;
        }}
        
        li {{
            margin-bottom: 8px;
        }}
        
        @media print {{
            body {{
                padding: 20px;
            }}
            .page-break {{
                page-break-before: always;
            }}
        }}
    </style>
</head>
<body>
    <header class="header">
        <div class="header-meta">Investment Due Diligence Report</div>
        <h1>{company_name}</h1>
        <div class="date">{current_date}</div>
    </header>
    
    <main>
        {sections_html if sections_html else f'<div class="report-section">{to_html(content)}</div>'}
    </main>
    
    <footer class="footer">
        <p>This report was generated on {current_date}.</p>
        <p>Confidential - For Investment Purposes Only</p>
    </footer>
</body>
</html>"""

            # Save report
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = (
                f"investment_report_{company_name.replace(' ', '_')}_{timestamp}.html"
            )
            filepath = os.path.join(get_output_dir(), filename)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(template)

            logger.info(f"HTML report generated: {filepath}")
            return {
                "status": "success",
                "message": f"Report saved: {filepath}",
                "data": {"filepath": filepath},
            }
        except Exception as e:
            logger.error(f"Error generating report: {e}")
            return {"status": "error", "message": f"Error generating report: {str(e)}"}

    return registry
