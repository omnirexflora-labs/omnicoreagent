#!/usr/bin/env python3
"""
Metrics & Observability Example

Track token usage, request counts, and response times.
Essential for cost monitoring and performance optimization.

Run:
    python cookbook/production/metrics_observability.py
"""

import asyncio
    agent = OmniCoreAgent(
        name="monitored_agent",
        system_instruction="You are a helpful assistant.",
        model_config={"provider": "openai", "model": "gpt-4o"}
    )

    # --- Per-Request Metrics ---
    print("=" * 50)
    print("PER-REQUEST METRICS")
    print("=" * 50)

    result = await agent.run("Explain quantum computing in simple terms")
    
    metric = result['metric']
    print(f"Query: 'Explain quantum computing...'")
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
    print("COST ESTIMATION (GPT-4o pricing)")
    print("=" * 50)

    # GPT-4o pricing (as of 2024)
    COST_PER_1K_INPUT = 0.0025   # $2.50 per 1M input tokens
    COST_PER_1K_OUTPUT = 0.01    # $10 per 1M output tokens

    # Simple estimation (actual breakdown would need per-request tracking)
    total_tokens = stats['total_tokens']
    estimated_cost = total_tokens / 1000 * ((COST_PER_1K_INPUT + COST_PER_1K_OUTPUT) / 2)
    print(f"Estimated cost for {total_tokens} tokens: ${estimated_cost:.4f}")

    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
