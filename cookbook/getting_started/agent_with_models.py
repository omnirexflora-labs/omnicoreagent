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
Set `LLM_API_KEY` for the provider you are running. The cookbook defaults to
OpenAI with `OMNICOREAGENT_MODEL=gpt-5.4-mini` so examples stay inexpensive.

Build on: first_agent.py
Next: agent_with_local_tools.py

Run:
    python cookbook/getting_started/agent_with_models.py
"""

import asyncio
import os

from omnicoreagent import OmniCoreAgent

from _bootstrap import get_model, model_config, require_llm_api_key, response_text


def enabled(flag_name: str) -> bool:
    return os.getenv(flag_name, "").lower() in {"1", "true", "yes"}


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
                "temperature": 0.7,
            },
        )

        result = await agent.run("Introduce yourself in one sentence.")
        print(f"Response: {response_text(result)}")
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
                "temperature": 0.5,
            },
        )

        result = await agent.run("What is special about the Gemini model?")
        print(f"Response: {response_text(result)}")
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
                "max_tokens": 512,
            },
        )

        result = await agent.run("Why is Groq so fast?")
        print(f"Response: {response_text(result)}")
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
                "ollama_host": "http://localhost:11434",  # Optional, defaults to env var or standard
            },
        )

        result = await agent.run("Are you running locally?")
        print(f"Response: {response_text(result)}")
        await agent.cleanup()
    except Exception as e:
        print(f"Skipping Ollama: {e}")


async def main():
    require_llm_api_key()

    print("Unified Configuration: using LLM_API_KEY with the selected provider.")

    # OpenAI is the default
    print("\n" + "=" * 50)
    print("OPENAI (DEFAULT)")
    print("=" * 50)
    try:
        agent = OmniCoreAgent(
            name="openai_agent",
            system_instruction=f"You are {get_model()}, a helpful assistant.",
            model_config=model_config(temperature=0.0, max_tokens=500),
        )
        result = await agent.run("Hello from OpenAI!")
        print(f"Response: {response_text(result)}")
        await agent.cleanup()
    except Exception as e:
        print(f"Skipping OpenAI: {e}")

    # Run other demos
    # NOTE: These will likely fail if your LLM_API_KEY is for OpenAI
    # and you try to call Anthropic, unless you have a unified key
    # or are changing the env var between runs.
    # But the code shows HOW to configure them.

    print("\n--- Optional provider demos ---")
    print("Set RUN_PROVIDER_DEMOS=1 to run Anthropic, Gemini, Groq, and Ollama demos.")
    if enabled("RUN_PROVIDER_DEMOS"):
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
