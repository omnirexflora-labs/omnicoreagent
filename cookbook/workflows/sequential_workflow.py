#!/usr/bin/env python3
"""
<<<<<<< HEAD
Sequential Workflow Example: Content Generation Pipeline

This example demonstrates a production-like sequential workflow for creating high-quality content.
The pipeline consists of three specialized agents:
1.  **Researcher**: Uses the Tavily MCP tool to gather real-time information.
2.  **Writer**: Drafts a comprehensive article based on the research.
3.  **Editor**: Reviews, refines, and formats the final piece.

Prerequisites:
    - TAVILY_API_KEY environment variable must be set.
    - Node.js (npx) installed for MCP tools.
=======
Sequential Workflow Example

Chain multiple agents where output of one becomes input for the next.
Example: Data Collector → Formatter → Reporter
>>>>>>> ee0f3ad (added cookbook getting started phase)

Run:
    python cookbook/workflows/sequential_workflow.py
"""

import asyncio
<<<<<<< HEAD
import logging
import os
import sys
from typing import List, Optional

=======
>>>>>>> ee0f3ad (added cookbook getting started phase)
from dotenv import load_dotenv

from omnicoreagent import (
    OmniCoreAgent,
    SequentialAgent,
    ToolRegistry,
    MemoryRouter,
    EventRouter,
)

<<<<<<< HEAD
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ContentPipeline")


def check_dependencies():
    """Verify all necessary dependencies and environment variables are present."""
    if not os.getenv("TAVILY_API_KEY"):
        logger.error("TAVILY_API_KEY environment variable is not set.")
        logger.error("Please sign up at https://tavily.com/ to get a free API key.")
        sys.exit(1)


def ensure_string(text: str | list) -> str:
    """Helper to ensure input is a string, handling lists from unpredictable LLM outputs."""
    if isinstance(text, list):
        return " ".join(str(x) for x in text)
    return str(text)


def create_writer_tools() -> ToolRegistry:
    """Tools for the writer agent to self-check their draft."""
    registry = ToolRegistry()

    @registry.register_tool("estimate_reading_time")
    def estimate_reading_time(text: str | list) -> str:
        """Estimate reading time based on word count (approx 200 wpm)."""
        text = ensure_string(text)
        word_count = len(text.split())
        minutes = max(1, round(word_count / 200))
        return f"Estimated reading time: {minutes} minute(s) ({word_count} words)."

    @registry.register_tool("check_markdown_structure")
    def check_markdown_structure(text: str | list) -> str:
        """Check if the text has proper Markdown headers (H1, H2)."""
        import re

        text = ensure_string(text)
        has_h1 = bool(re.search(r"^#\s+.+", text, re.MULTILINE))
        has_h2 = bool(re.search(r"^##\s+.+", text, re.MULTILINE))

        issues = []
        if not has_h1:
            issues.append("Missing main title (H1 '# Title').")
        if not has_h2:
            issues.append("Missing section headers (H2 '## Section').")

        if not issues:
            return "Structure check passed: H1 and H2 headers present."
        return "Structure issues found:\n- " + "\n- ".join(issues)

    return registry


def create_editor_tools() -> ToolRegistry:
    """Tools for the editor agent."""
    registry = ToolRegistry()

    @registry.register_tool("analyze_content_quality")
    def analyze_content_quality(text: str | list) -> str:
        """
        Analyze the text for quality metrics:
        - Word count and sentence length.
        - Identifies overly long sentences (>25 words).
        - Identifies huge paragraphs (>150 words).
        Returns a structured JSON-like report.
        """
        import json
        import re

        text = ensure_string(text)

        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        sentences = re.split(r"[.!?]+", text)
        sentences = [s.strip() for s in sentences if s.strip()]
        words = text.split()

        avg_sentence_length = len(words) / max(1, len(sentences))

        long_sentences = [s for s in sentences if len(s.split()) > 25]
        long_paragraphs = [p for p in paragraphs if len(p.split()) > 150]

        score = 100
        issues = []

        if avg_sentence_length > 20:
            score -= 10
            issues.append("Average sentence length is too high (>20 words).")

        if long_sentences:
            score -= len(long_sentences) * 2
            issues.append(f"Found {len(long_sentences)} complex sentences (>25 words).")

        if long_paragraphs:
            score -= len(long_paragraphs) * 5
            issues.append(
                f"Found {len(long_paragraphs)} massive paragraphs (>150 words)."
            )

        report = {
            "metrics": {
                "word_count": len(words),
                "sentence_count": len(sentences),
                "paragraph_count": len(paragraphs),
                "avg_sentence_length": round(avg_sentence_length, 1),
            },
            "quality_score": max(0, score),
            "issues": issues,
        }

        return json.dumps(report, indent=2)

    return registry


async def create_pipeline() -> SequentialAgent:
    """Initialize and configure the multi-agent pipeline."""
    tavily_api_key = os.getenv("TAVILY_API_KEY")

    # --- Agent 1: Researcher (MCP Enabled) ---
    logger.info("Initializing Researcher Agent with Tavily MCP...")
    researcher = OmniCoreAgent(
        name="Researcher",
        system_instruction=(
            "You are an expert web researcher. "
            "Use the 'tavily_search' tool to find detailed and accurate information on the user's topic. "
            "Focus on finding recent data, statistics, and reputable sources. "
            "Summarize your findings clearly."
        ),
        model_config={"provider": "cencori", "model": "gpt-4o"},
        mcp_tools=[
            {
                "name": "tavily-remote-mcp",
                "transport_type": "stdio",
                "command": "npx",
                "args": [
                    "-y",
                    "mcp-remote",
                    f"https://mcp.tavily.com/mcp/?tavilyApiKey={tavily_api_key}",
                ],
            }
        ],
        memory_router=MemoryRouter("in_memory"),
        event_router=EventRouter("in_memory"),
        debug=True,
    )

    # --- Agent 2: Writer ---
    logger.info("Initializing Writer Agent...")
    writer = OmniCoreAgent(
        name="Writer",
        system_instruction=(
            "You are a skilled content writer. "
            "Create a well-structured draft article that engages the reader. "
            "Before finishing, use 'check_markdown_structure' to verify your headers "
            "and 'estimate_reading_time' to ensure it's substantial enough. "
            "Base your content *strictly* on the provided research."
        ),
        model_config={"provider": "cencori", "model": "gpt-4o"},
        local_tools=create_writer_tools(),
        memory_router=MemoryRouter("in_memory"),
        event_router=EventRouter("in_memory"),
        debug=True,
    )

    # --- Agent 3: Editor ---
    logger.info("Initializing Editor Agent...")
    editor = OmniCoreAgent(
        name="Editor",
        system_instruction=(
            "You are a strict editor. Review the draft article. "
            "Use 'analyze_content_quality' to get a quantitative report on the text. "
            "If the score is below 90, rewrite the low-quality sections to fix the reported issues "
            "(e.g., shorten sentences, break up paragraphs). "
            "Also ensure grammar and tone are perfect."
        ),
        model_config={"provider": "cencori", "model": "gpt-4o"},
        local_tools=create_editor_tools(),
        memory_router=MemoryRouter("in_memory"),
        event_router=EventRouter("in_memory"),
        debug=True,
    )

    return SequentialAgent(sub_agents=[researcher, writer, editor])
=======

def create_collector_tools() -> ToolRegistry:
    """Tools for the data collector agent."""
    registry = ToolRegistry()

    @registry.register_tool("get_system_info")
    def get_system_info() -> str:
        """Get current system information."""
        import platform
        import time

        return (
            f"OS: {platform.system()} {platform.release()}\n"
            f"Python: {platform.python_version()}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    return registry


def create_formatter_tools() -> ToolRegistry:
    """Tools for the text formatter agent."""
    registry = ToolRegistry()

    @registry.register_tool("format_text")
    def format_text(text: str, style: str = "uppercase") -> str:
        """Format text in a specific style (uppercase, lowercase, title)."""
        if style == "uppercase":
            return text.upper()
        elif style == "lowercase":
            return text.lower()
        elif style == "title":
            return text.title()
        return text

    return registry


# Agent 1: Collects data
data_collector = OmniCoreAgent(
    name="DataCollector",
    system_instruction="Collect system information using the get_system_info tool.",
    model_config={"provider": "openai", "model": "gpt-4o"},
    local_tools=create_collector_tools(),
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
)

# Agent 2: Formats the data
text_formatter = OmniCoreAgent(
    name="TextFormatter",
    system_instruction="Format the input text to uppercase using the format_text tool.",
    model_config={"provider": "openai", "model": "gpt-4o"},
    local_tools=create_formatter_tools(),
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
)

# Agent 3: Creates final report
reporter = OmniCoreAgent(
    name="Reporter",
    system_instruction="Summarize the input into a brief final report.",
    model_config={"provider": "openai", "model": "gpt-4o"},
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
)

# Create the sequential workflow
<<<<<<< HEAD
workflow = SequentialAgent(
    sub_agents=[data_collector, text_formatter, reporter]
)
>>>>>>> ee0f3ad (added cookbook getting started phase)
=======
workflow = SequentialAgent(sub_agents=[data_collector, text_formatter, reporter])
>>>>>>> 5d48e69 (support cencori)


async def main():
    load_dotenv()
<<<<<<< HEAD
    check_dependencies()

    workflow: Optional[SequentialAgent] = None

    try:
        workflow = await create_pipeline()
        await workflow.initialize()
        logger.info("Workflow pipeline initialized.")

        topic = "The Future of AI Agents in Software Development"
        logger.info(f"Starting workflow for topic: {topic}")

        # Run the workflow
        result = await workflow.run(
            initial_task=f"Research and write a comprehensive article about: {topic}",
            session_id="prod_content_session_01",
        )

        logger.info("Workflow completed successfully.")
        print("\n" + "#" * 50)
        print("FINAL DELIVERABLE")
        print("#" * 50 + "\n")
        print(result["response"])
        print("\n" + "#" * 50)

    except Exception as e:
        logger.error(f"Workflow execution failed: {e}", exc_info=True)
    finally:
        if workflow:
            logger.info("Shutting down workflow...")
            await workflow.shutdown()
            logger.info("Shutdown complete.")
=======

    try:
        # Initialize all agents
        await workflow.initialize()
        print("Workflow initialized!")

        # Run the workflow
        print("\nRunning sequential workflow...")
        result = await workflow.run(
            initial_task="Get system information and create a formatted report",
            session_id="demo_session",
        )

        print(f"\nFinal Result:\n{result}")

    finally:
        # Clean up all agents
        await workflow.shutdown()
        print("\nWorkflow shut down.")
>>>>>>> ee0f3ad (added cookbook getting started phase)


if __name__ == "__main__":
    asyncio.run(main())
