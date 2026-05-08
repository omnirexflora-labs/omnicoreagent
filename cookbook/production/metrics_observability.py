#!/usr/bin/env python3
"""
Metrics & Observability Example

Track token usage, request counts, and response times.
Essential for cost monitoring and performance optimization.

Run:
    python cookbook/production/metrics_observability.py
"""

import asyncio

from omnicoreagent import OmniCoreAgent

from _bootstrap import model_config, require_llm_api_key


async def main():
    require_llm_api_key()

    agent = OmniCoreAgent(
        name="monitored_agent",
        system_instruction="You are a helpful assistant.",
        model_config=model_config(max_tokens=500),
    )

    # --- Per-Request Metrics ---
    print("=" * 50)
    print("PER-REQUEST METRICS")
    print("=" * 50)

    result = await agent.run("Explain quantum computing in simple terms")

    metric = result["metric"]
    print("Query: 'Explain quantum computing...'")
    print(f"  Request Tokens: {metric.request_tokens}")
    print(f"  Response Tokens: {metric.response_tokens}")
    print(f"  Total Time: {metric.total_time:.2f}s")

    # --- Run a few more queries ---
    await agent.run("What is machine learning?")
    await agent.run("Explain neural networks")

    # --- Cumulative Metrics ---
    print("\n" + "=" * 50)
    print("CUMULATIVE METRICS")
    print("=" * 50)

    stats = await agent.get_metrics()
    print(f"Total Requests: {stats['total_requests']}")
    print(f"Total Tokens: {stats['total_tokens']}")
    print(f"Average Time: {stats['average_time']:.2f}s")

    # --- Cost Estimation Example ---
    print("\n" + "=" * 50)
    print("COST ESTIMATION")
    print("=" * 50)

    total_tokens = stats["total_tokens"]
    print(f"Total tokens to price with your provider's current rates: {total_tokens}")

    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
