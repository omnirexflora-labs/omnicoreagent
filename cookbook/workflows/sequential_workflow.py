#!/usr/bin/env python3
"""
Sequential Workflow Example: Content Generation Pipeline

This example demonstrates a production-like sequential workflow for creating high-quality content.
The pipeline consists of three specialized agents:
1.  **Researcher**: Uses a local research tool to gather source material.
2.  **Writer**: Drafts a comprehensive article based on the research.
3.  **Editor**: Reviews, refines, and formats the final piece.

Run:
    python cookbook/workflows/sequential_workflow.py
"""

import asyncio
import logging
import sys
from typing import Optional

from omnicoreagent import (
    OmniCoreAgent,
    SequentialAgent,
    ToolRegistry,
    MemoryRouter,
    EventRouter,
)

from _bootstrap import model_config as cookbook_model_config
from _bootstrap import require_llm_api_key

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ContentPipeline")


def check_dependencies():
    """Verify all necessary dependencies and environment variables are present."""
    try:
        require_llm_api_key()
    except RuntimeError as exc:
        logger.error(str(exc))
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


def create_research_tools() -> ToolRegistry:
    """Tools for gathering grounded source notes for the researcher agent."""
    registry = ToolRegistry()

    @registry.register_tool("lookup_source_notes")
    def lookup_source_notes(topic: str) -> str:
        """Return source notes from an internal research corpus for a topic."""
        return (
            f"Research notes for {topic}:\n"
            "- AI coding agents combine model reasoning, tool execution, memory, and workspace files.\n"
            "- Production agent systems need guardrails, loop detection, event streams, and metrics.\n"
            "- Teams adopt agents faster when examples show concrete tool use and operational boundaries.\n"
            "- Strong agent harnesses keep tool output structured so the model receives clean signal."
        )

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
    # This workflow passes trusted output from one internal agent to the next.
    # Boundary guardrails belong on external user input; internal handoffs should
    # not be blocked just because they contain system-like words from analysis.
    internal_agent_config = {
        "guardrail_mode": "off",
        "max_steps": 8,
        "tool_call_timeout": 20,
    }

    # --- Agent 1: Researcher ---
    logger.info("Initializing Researcher Agent...")
    researcher = OmniCoreAgent(
        name="Researcher",
        system_instruction=(
            "You are an expert web researcher. "
            "Use 'lookup_source_notes' before writing your research summary. "
            "Focus on concrete technical points and source-backed claims. "
            "After the tool returns, immediately provide the research notes as your final answer. "
            "Do not ask for another topic; the topic is already in the current task."
        ),
        model_config=cookbook_model_config(max_tokens=700),
        agent_config=internal_agent_config,
        local_tools=create_research_tools(),
        memory_router=MemoryRouter("in_memory"),
        event_router=EventRouter("in_memory"),
        debug=False,
    )

    # --- Agent 2: Writer ---
    logger.info("Initializing Writer Agent...")
    writer = OmniCoreAgent(
        name="Writer",
        system_instruction=(
            "You are a skilled content writer. "
            "Turn the provided research notes into a complete Markdown article. "
            "Use an H1 title, 3 H2 sections, and a short closing section. "
            "Use 'check_markdown_structure' and 'estimate_reading_time' on your draft before the final answer. "
            "Base your content *strictly* on the provided research."
        ),
        model_config=cookbook_model_config(max_tokens=800),
        agent_config=internal_agent_config,
        local_tools=create_writer_tools(),
        memory_router=MemoryRouter("in_memory"),
        event_router=EventRouter("in_memory"),
        debug=False,
    )

    # --- Agent 3: Editor ---
    logger.info("Initializing Editor Agent...")
    editor = OmniCoreAgent(
        name="Editor",
        system_instruction=(
            "You are a strict editor. Review the draft article. "
            "Use 'analyze_content_quality' to get a quantitative report on the text. "
            "If the score is below 90, rewrite the low-quality sections to fix the reported issues "
            "(e.g., shorten sentences, break up paragraphs). Return the final edited article only. "
            "Do not ask for the draft; the current input is the draft."
        ),
        model_config=cookbook_model_config(max_tokens=800),
        agent_config=internal_agent_config,
        local_tools=create_editor_tools(),
        memory_router=MemoryRouter("in_memory"),
        event_router=EventRouter("in_memory"),
        debug=False,
    )

    return SequentialAgent(sub_agents=[researcher, writer, editor])


async def main():
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


if __name__ == "__main__":
    asyncio.run(main())
