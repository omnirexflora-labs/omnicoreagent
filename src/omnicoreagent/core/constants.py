from datetime import datetime, timezone

TOOL_ACCEPTING_PROVIDERS = {
    "groq",
    "openai",
    "openrouter",
    "gemini",
    "deepseek",
    "azureopenai",
    "anthropic",
    "mistral",
}

AGENTS_REGISTRY = {}
TOOLS_REGISTRY = {}
date_time_func = {
    "format_date": lambda data=None: datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
}
