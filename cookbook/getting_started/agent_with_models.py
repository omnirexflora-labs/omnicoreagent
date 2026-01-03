#!/usr/bin/env python3
"""
Agent with Different Models

OmniCoreAgent supports multiple LLM providers:
- OpenAI (default)
- Anthropic (Claude)
- Google Gemini
- Groq
- Ollama (local models)
- Azure OpenAI
- DeepSeek
- Mistral
- OpenRouter

Unified Configuration:
Regardless of the provider, you only need to set `LLM_API_KEY` in your `.env` file.
OmniCoreAgent automatically maps this key to the specific provider you choose.

Build on: first_agent.py
Next: agent_with_local_tools.py

Run:
    python cookbook/getting_started/agent_with_models.py
"""

import asyncio
import os
import sys

from omnicoreagent import OmniCoreAgent


async def demo_anthropic():
    """Demo using Anthropic Claude."""
    print("\n" + "=" * 50)
    print("ANTHROPIC (CLAUDE 3.5 SONNET)")
    print("=" * 50)

    try:
        agent = OmniCoreAgent(
            name="claude_agent",
            system_instruction="You are Claude, a helpful assistant.",
            model_config={
                "provider": "anthropic",
                "model": "claude-3-5-sonnet-20240620",
                "max_tokens": 1024,
                "temperature": 0.7
            }
        )

        result = await agent.run("Introduce yourself in one sentence.")
        print(f"Response: {result['response']}")
        await agent.cleanup()
    except Exception as e:
        print(f"Skipping Anthropic: {e}")
        print("(Make sure your LLM_API_KEY is valid for Anthropic)")


async def demo_gemini():
    """Demo using Google Gemini."""
    print("\n" + "=" * 50)
    print("GOOGLE GEMINI (1.5 FLASH)")
    print("=" * 50)

    try:
        agent = OmniCoreAgent(
            name="gemini_agent",
            system_instruction="You are Gemini.",
            model_config={
                "provider": "gemini",
                "model": "gemini-1.5-flash",
                "temperature": 0.5
            }
        )

        result = await agent.run("What is special about the Gemini model?")
        print(f"Response: {result['response']}")
        await agent.cleanup()
    except Exception as e:
        print(f"Skipping Gemini: {e}")
        print("(Make sure your LLM_API_KEY is valid for Gemini)")


async def demo_groq():
    """Demo using Groq (Fast Inference)."""
    print("\n" + "=" * 50)
    print("GROQ (LLAMA 3)")
    print("=" * 50)

    try:
        agent = OmniCoreAgent(
            name="groq_agent",
            system_instruction="You are a super fast assistant.",
            model_config={
                "provider": "groq",
                "model": "llama3-70b-8192",
                "max_tokens": 512
            }
        )

        result = await agent.run("Why is Groq so fast?")
        print(f"Response: {result['response']}")
        await agent.cleanup()
    except Exception as e:
        print(f"Skipping Groq: {e}")
        print("(Make sure your LLM_API_KEY is valid for Groq)")


async def demo_ollama():
    """Demo using Ollama (Local Models)."""
    print("\n" + "=" * 50)
    print("OLLAMA (LOCAL LLAMA3)")
    print("=" * 50)

    # Ollama doesn't usually need an API key, but the agent framework
    # expects LLM_API_KEY to be present for initialization checks.
    
    print("Note: Ensure you have pulled the model first: `ollama pull llama3`")

    try:
        agent = OmniCoreAgent(
            name="ollama_agent",
            system_instruction="You are a local AI.",
            model_config={
                "provider": "ollama",
                "model": "llama3",  # Must match a pulled model
                "ollama_host": "http://localhost:11434" # Optional, defaults to env var or standard
            }
        )

        result = await agent.run("Are you running locally?")
        print(f"Response: {result['response']}")
        await agent.cleanup()
    except Exception as e:
        print(f"Skipping Ollama: {e}")


async def main():
    # Check if LLM_API_KEY is set
    if not os.getenv("LLM_API_KEY"):
        print("ERROR: LLM_API_KEY environment variable is not set.")
        print("Please set it in your .env file or environment.")
        print("Example: LLM_API_KEY=sk-...")
        return

    print("Unified Configuration: Using LLM_API_KEY for all providers.")

    # OpenAI is the default
    print("\n" + "=" * 50)
    print("OPENAI (DEFAULT)")
    print("=" * 50)
    try:
        agent = OmniCoreAgent(
            name="openai_agent",
            system_instruction="You are GPT-4o.",
            model_config={
                "provider": "openai",
                "model": "gpt-4o",
                "temperature": 0.0
            }
        )
        result = await agent.run("Hello from OpenAI!")
        print(f"Response: {result['response']}")
        await agent.cleanup()
    except Exception as e:
        print(f"Skipping OpenAI: {e}")

    # Run other demos
    # NOTE: These will likely fail if your LLM_API_KEY is for OpenAI 
    # and you try to call Anthropic, unless you have a unified key 
    # or are changing the env var between runs.
    # But the code shows HOW to configure them.
    
    print("\n--- Demonstrating other providers (requires valid keys) ---")
    await demo_anthropic()
    await demo_gemini()
    await demo_groq()
    await demo_ollama()
    
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print("""
OmniCoreAgent unifies API key management. 
You set `LLM_API_KEY` for the provider you want to use.
The framework automatically maps it:

LLM_API_KEY -> OPENAI_API_KEY (if provider="openai")
LLM_API_KEY -> ANTHROPIC_API_KEY (if provider="anthropic")
LLM_API_KEY -> GEMINI_API_KEY (if provider="gemini")
...and so on.
""")


if __name__ == "__main__":
    asyncio.run(main())
