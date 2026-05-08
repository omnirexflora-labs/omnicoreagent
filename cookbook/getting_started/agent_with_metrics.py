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

from _bootstrap import model_config, require_llm_api_key, response_text


async def main():
    require_llm_api_key()

    print("=" * 60)
    print("AGENT METRICS - Monitor Performance and Usage")
    print("=" * 60)

    agent = OmniCoreAgent(
        name="monitored_agent",
        system_instruction="You are a helpful assistant. Keep responses brief.",
        model_config=model_config(max_tokens=500),
        agent_config={
            "request_limit": 100,  # Limit requests for safety
            "total_tokens_limit": 0,  # 0 = unlimited; set a budget in production
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
        print(f"Response: {response_text(result)[:100]}...")

        # Get metrics after each call
        metrics = await agent.get_metrics()

        print(f"\n📈 Metrics after query {i}:")
        print(f"  Total Requests: {metrics.get('total_requests', 0)}")
        print(f"  Total Tokens: {metrics.get('total_tokens', 0)}")
        print(f"  Request Tokens: {metrics.get('total_request_tokens', 0)}")
        print(f"  Response Tokens: {metrics.get('total_response_tokens', 0)}")

    # Final metrics summary
    final_metrics = await agent.get_metrics()

    print("\n" + "=" * 60)
    print("FINAL METRICS SUMMARY")
    print("=" * 60)
    print(f"""
📊 Session Statistics:
  • Total Requests: {final_metrics.get("total_requests", 0)}
  • Total Tokens Used: {final_metrics.get("total_tokens", 0)}
  • Request Tokens: {final_metrics.get("total_request_tokens", 0)}
  • Response Tokens: {final_metrics.get("total_response_tokens", 0)}
  
💰 Cost Estimation:
  • Use the token counts above with your provider's current pricing.
""")

    print("=" * 60)
    print("AVAILABLE METRICS")
    print("=" * 60)
    print("""
await agent.get_metrics() returns:

{
    "total_requests": int,      # Number of agent.run() calls
    "total_tokens": int,        # Total tokens used (in + out)
    "total_request_tokens": int,  # Tokens sent to LLM
    "total_response_tokens": int, # Tokens received from LLM
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
