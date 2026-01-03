#!/usr/bin/env python3
"""
Agent Configuration (Advanced)

OmniCoreAgent is highly configurable.
This example shows how to tune performance, set limits, and enable advanced features.

Features covered:
- Timeout settings
- Step limits (loop prevention)
- Token usage limits
- Agent Skills
- Advanced Tool Use

Build on: agent_with_event_switching.py
This is a reference for advanced usage.

Run:
    python cookbook/getting_started/agent_configuration.py
"""

import asyncio

from omnicoreagent import OmniCoreAgent


async def main():

    print("=" * 50)
    print("ADVANCED AGENT CONFIGURATION")
    print("=" * 50)

    # Define a robust configuration
    agent_config = {
        # === PRODUCTION SAFETY ===
        "request_limit": 100,         # Limit total requests per session (0=unlimited)
        "total_tokens_limit": 100000, # Limit total tokens used (cost control)
        
        # === EXECUTION CONTROL ===
        "max_steps": 10,              # Max thinking steps before giving up (prevent loops)
        "tool_call_timeout": 30,      # Seconds to wait for a tool to finish
        
        # === FEATURE FLAGS ===
        "enable_advanced_tool_use": True, # Enable smarter tool selection logic
        "enable_agent_skills": True,      # Enable specialized agent skills system
        
        # === MEMORY SETTINGS ===
        "memory_config": {
            "mode": "sliding_window", # How to manage context window
            "value": 10               # Number of messages to keep
        },
        
       
    }

    print("\nInitializing agent with config:")
    for k, v in agent_config.items():
        print(f"  {k}: {v}")

    agent = OmniCoreAgent(
        name="configured_agent",
        system_instruction="You are a precisely configured agent.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        agent_config=agent_config,  # <- Pass configuration dict here
        debug=True                  # <- Enable debug logging for development
    )

    print("\nRunning query with constraints...")
    # This query requires multiple steps (thinking + answering), protecting against infinite loops
    try:
        result = await agent.run("Calculate the square root of 144, then multiply it by 5.")
        print(f"Response: {result['response']}")
        
        # Check specific metrics if available
        # (Implementation detail: usage tracking happens internally)
        
    except Exception as e:
        print(f"Agent execution failed: {e}")

    await agent.cleanup()
    
    print("\n" + "=" * 50)
    print("CONFIGURATION REFERENCE")
    print("=" * 50)
    print("""
| Parameter          | Default | Description                                      |
|--------------------|---------|--------------------------------------------------|
| max_steps          | 10      | Max reasoning loops (prevent infinite loops)     |
| tool_call_timeout  | 60      | Max seconds for a tool to run                    |
| request_limit      | 0       | Max requests per session (0 = unlimited)         |
| total_tokens_limit | 0       | Max tokens usage limit (cost safety)             |
| enable_agent_skills| False   | Enable specialized skill libraries               |
| debug              | False   | Print verbose logs (passed to init, not config)  |
""")


if __name__ == "__main__":
    asyncio.run(main())
