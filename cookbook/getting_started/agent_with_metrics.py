#!/usr/bin/env python3
"""
Agent Metrics Example

OmniCoreAgent tracks detailed metrics for monitoring and debugging.
This example shows how to access and use agent metrics in production.

Features covered:
- Getting runtime metrics
- Token usage tracking
- Request counting
- Performance monitoring

Build on: agent_with_guardrails.py
Essential for production monitoring and cost control.

Run:
    python cookbook/getting_started/agent_with_metrics.py
"""

import asyncio

from omnicoreagent import OmniCoreAgent


async def main():
    print("=" * 60)
    print("AGENT METRICS - Monitor Performance and Usage")
    print("=" * 60)

    agent = OmniCoreAgent(
        name="monitored_agent",
        system_instruction="You are a helpful assistant. Keep responses brief.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        agent_config={
            "request_limit": 100,  # Limit requests for safety
            "total_tokens_limit": 50000,  # Limit tokens for cost control
        },
        debug=True,
    )

    print("\n📊 Running queries and tracking metrics...")

    # Run a few queries
    queries = [
        "What is Python?",
        "What is JavaScript?",
        "Compare Python and JavaScript in one sentence.",
    ]

    for i, query in enumerate(queries, 1):
        print(f"\n--- Query {i}/{len(queries)} ---")
        result = await agent.run(query)
        print(f"Response: {result.get('response', '')[:100]}...")
        
        # Get metrics after each call
        metrics = await agent.get_metrics()
        
        print(f"\n📈 Metrics after query {i}:")
        print(f"  Total Requests: {metrics.get('total_requests', 0)}")
        print(f"  Total Tokens: {metrics.get('total_tokens', 0)}")
        print(f"  Input Tokens: {metrics.get('total_input_tokens', 0)}")
        print(f"  Output Tokens: {metrics.get('total_output_tokens', 0)}")

    # Final metrics summary
    final_metrics = await agent.get_metrics()
    
    print("\n" + "=" * 60)
    print("FINAL METRICS SUMMARY")
    print("=" * 60)
    print(f"""
📊 Session Statistics:
  • Total Requests: {final_metrics.get('total_requests', 0)}
  • Total Tokens Used: {final_metrics.get('total_tokens', 0)}
  • Input Tokens: {final_metrics.get('total_input_tokens', 0)}
  • Output Tokens: {final_metrics.get('total_output_tokens', 0)}
  
💰 Cost Estimation (GPT-4o pricing):
  • Input: ~${final_metrics.get('total_input_tokens', 0) * 0.000005:.4f}
  • Output: ~${final_metrics.get('total_output_tokens', 0) * 0.000015:.4f}
""")

    print("=" * 60)
    print("AVAILABLE METRICS")
    print("=" * 60)
    print("""
await agent.get_metrics() returns:

{
    "total_requests": int,      # Number of agent.run() calls
    "total_tokens": int,        # Total tokens used (in + out)
    "total_input_tokens": int,  # Tokens sent to LLM
    "total_output_tokens": int, # Tokens received from LLM
}

Use Cases:
  • Cost tracking and budgeting
  • Rate limit enforcement  
  • Performance monitoring
  • Usage analytics per user/session
""")

    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
