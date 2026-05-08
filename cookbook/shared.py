"""Shared helpers for OmniCoreAgent cookbook examples."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-5.4-mini"


def load_cookbook_env() -> None:
    """Load the repository .env file for cookbook examples."""
    load_dotenv(ROOT_DIR / ".env")
    load_dotenv()


def get_provider(default: str = DEFAULT_PROVIDER) -> str:
    load_cookbook_env()
    return os.getenv("OMNICOREAGENT_PROVIDER", default)


def get_model(default: str = DEFAULT_MODEL) -> str:
    load_cookbook_env()
    return (
        os.getenv("OMNICOREAGENT_MODEL")
        or os.getenv("OMNICOREAGENT_TEST_MODEL")
        or os.getenv("OPENAI_MODEL")
        or default
    )


def model_config(
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = 0.2,
    max_tokens: int | None = 1200,
    **overrides,
) -> dict:
    """Return a low-cost model config suitable for examples and tests."""
    load_cookbook_env()
    config = {
        "provider": provider or get_provider(),
        "model": model or get_model(),
    }
    if temperature is not None:
        config["temperature"] = temperature
    if max_tokens is not None:
        config["max_tokens"] = max_tokens
    config.update({key: value for key, value in overrides.items() if value is not None})
    return config


def require_llm_api_key() -> None:
    """Fail early with a direct message when hosted model credentials are missing."""
    load_cookbook_env()
    provider = get_provider()
    if provider == "ollama":
        return
    if not os.getenv("LLM_API_KEY"):
        raise RuntimeError(
            "LLM_API_KEY is required. Add it to .env or export it before running "
            "cookbook examples."
        )


def response_text(result: dict) -> str:
    """Extract the response string from agent.run() output."""
    return str(result.get("response", ""))
