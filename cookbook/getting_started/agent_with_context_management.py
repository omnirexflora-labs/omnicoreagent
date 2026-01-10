#!/usr/bin/env python3
"""
Context Management Example

OmniCoreAgent automatically manages context for long-running conversations.
This prevents token exhaustion and keeps agents running indefinitely.

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


async def main():
    print("=" * 60)
    print("CONTEXT MANAGEMENT - Handle Infinitely Long Conversations")
    print("=" * 60)

    # Context management prevents token exhaustion in long conversations
    # Without it, agents eventually hit token limits and fail
    agent = OmniCoreAgent(
        name="context_managed_agents",
        system_instruction="""You are a research assistant helping with a long project.
You remember context from our entire conversation, even as it grows very long.""",
        model_config={"provider": "openai", "model": "gpt-4o"},
        agent_config={
            # === CONTEXT MANAGEMENT ===
            # This is the "magic" that lets agents run forever
            "context_management": {
                "enabled": True,  # Turn on automatic context management
                "mode": "token_budget",  # Can be "token_budget" or "sliding_window"
                "value": 10000,  # Max tokens before triggering management
                "threshold_percent": 75,  # Trigger at 75% of limit
                "strategy": "summarize_and_truncate",  # or just "truncate"
                "preserve_recent": 6,  # Always keep last 6 messages
            },
            # === MEMORY SUMMARIZATION (complementary feature) ===
            "memory_config": {
                "mode": "token_budget",
                "value": 50000,  # Keep last 50 messages in memory
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
    print("  • Threshold: 75% of 100K tokens")
    print("  • Strategy: summarize_and_truncate (smart compression)")
    print("  • Preserve: Last 6 messages always kept intact")

    # Simulate a LONG conversation (30 messages) to test both memory and context management
    messages = [
        # Initial exploration
        "Let's research AI trends. Start by listing the top 5 AI trends in 2024.",
        "Tell me more about trend #1 - generative AI.",
        "What about multimodal AI? How is it different from generative AI?",
        "How do these AI trends compare to what we saw in 2023?",
        "Summarize what we've discussed so far about AI trends.",
        
        # Deep dive into specific topics
        "Let's focus on LLMs. What are the leading LLM providers right now?",
        "Compare GPT-4 vs Claude vs Gemini - what are the key differences?",
        "Which one is best for code generation specifically?",
        "What about cost? Compare the pricing models of these providers.",
        "Summarize the LLM comparison we just did.",
        
        # New direction - applications
        "Now let's talk about AI applications in healthcare.",
        "What are the regulatory challenges for AI in healthcare?",
        "How does HIPAA affect AI deployment in hospitals?",
        "Give me 3 examples of successful AI healthcare deployments.",
        "What about AI in medical imaging specifically?",
        
        # Testing memory recall
        "Earlier we discussed AI trends - can you remind me of the top 5?",
        "How does the healthcare AI we just discussed relate to those trends?",
        "Which of the LLM providers we compared would be best for healthcare?",
        
        # More depth
        "Let's discuss AI safety and alignment.",
        "What are the main concerns with AI safety today?",
        "How do companies like Anthropic approach AI safety?",
        "What is constitutional AI and how does it work?",
        "Compare the safety approaches of OpenAI vs Anthropic vs Google.",
        
        # Final synthesis
        "Let's bring it all together. Based on everything we discussed...",
        "What's the future of AI in 2025 based on these trends?",
        "What should businesses focus on to leverage AI effectively?",
        "Give me a final summary of our entire conversation.",
        "What were the 3 most important insights from our discussion?",
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
  ✅ Conversations can run indefinitely
  ✅ Old context is summarized, not lost
  ✅ Recent messages stay intact for accuracy
  ✅ Token usage stays within budget
""")

    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
