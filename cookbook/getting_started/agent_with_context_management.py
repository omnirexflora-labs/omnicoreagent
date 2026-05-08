#!/usr/bin/env python3
"""
Context Management Example

OmniCoreAgent automatically manages context for long-running conversations.
This prevents token exhaustion by summarizing or truncating older context before
the prompt grows beyond the configured budget.

Features covered:
- Context management configuration
- Token budget mode
- Summarize and truncate strategy
- Monitoring context usage

Build on: agent_configuration.py
This is critical for production agents with long conversations.

Run:
    python cookbook/getting_started/agent_with_context_management.py
"""

import asyncio

from omnicoreagent import OmniCoreAgent

from _bootstrap import model_config, require_llm_api_key


async def main():
    require_llm_api_key()

    print("=" * 60)
    print("CONTEXT MANAGEMENT - Handle Long Conversations")
    print("=" * 60)

    # Context management prevents token exhaustion in long conversations
    # Without it, agents eventually hit token limits and fail
    agent = OmniCoreAgent(
        name="context_managed_agents",
        system_instruction="""You are a research assistant helping with a long project.
You remember context from our entire conversation, even as it grows very long.""",
        model_config=model_config(max_tokens=400),
        agent_config={
            # === CONTEXT MANAGEMENT ===
            # Keep long sessions inside a configured context budget.
            "context_management": {
                "enabled": True,  # Turn on automatic context management
                "mode": "token_budget",  # Can be "token_budget" or "sliding_window"
                "value": 3000,  # Low budget so the recipe demonstrates management quickly
                "threshold_percent": 75,  # Trigger at 75% of limit
                "strategy": "summarize_and_truncate",  # or just "truncate"
                "preserve_recent": 4,  # Always keep the latest messages intact
            },
            # === MEMORY SUMMARIZATION (complementary feature) ===
            "memory_config": {
                "mode": "token_budget",
                "value": 12000,
                "summary": {
                    "enabled": False,  # Summarize old messages
                    "retention_policy": "keep",  # Keep summaries
                },
            },
        },
        debug=True,
    )

    print("\n📊 Context Management Configuration:")
    print("  • Mode: token_budget (manage based on token count)")
    print("  • Threshold: 75% of 3,000 tokens")
    print("  • Strategy: summarize_and_truncate (smart compression)")
    print("  • Preserve: Last 4 messages always kept intact")

    # Simulate a long conversation to test both memory and context management.
    # Keep this short enough to run as a real cookbook example.
    messages = [
        # Initial exploration
        "Let's research AI trends. Start by listing the top 5 AI trends in 2024.",
        "Tell me more about trend #1 - generative AI.",
        "What about multimodal AI? How is it different from generative AI?",
        "Now let's talk about AI applications in healthcare.",
        "Earlier we discussed AI trends - can you remind me of the top 5?",
        # Final synthesis
        "Give me a final summary of our entire conversation.",
    ]

    print("\n🔄 Starting conversation simulation...")
    for i, msg in enumerate(messages, 1):
        print(f"\n--- Message {i}/{len(messages)} ---")
        print(f"User: {msg[:50]}...")

        result = await agent.run(msg, session_id="test_session")
        response = result.get("response", "")
        print(f"Agent: {response[:200]}...")

        # Show metrics
        metrics = await agent.get_metrics()
        print(f"📈 Tokens used: {metrics.get('total_tokens', 'N/A')}")

    # Final summary
    print("\n" + "=" * 60)
    print("WHY CONTEXT MANAGEMENT MATTERS")
    print("=" * 60)
    print("""
Without context management:
  ❌ Long conversations hit token limits and crash
  ❌ You lose context after ~30 messages
  ❌ Must manually truncate or restart

With context management:
  ✅ Long sessions stay within the configured context budget
  ✅ Old context is summarized, not lost
  ✅ Recent messages stay intact for accuracy
  ✅ Token usage stays within budget
""")

    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
