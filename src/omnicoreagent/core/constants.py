from datetime import datetime, timezone

SUPPORTED_MODELS_PROVIDERS = {
    "openai": "openai",
    "anthropic": "anthropic",
    "groq": "groq",
    "ollama": "ollama",
    "azure": "azure",
    "gemini": "gemini",
    "deepseek": "deepseek",
    "mistral": "mistral",
    "openrouter": "openrouter",
    "cencori": "cencori",
}

AGENTS_REGISTRY = {}
TOOLS_REGISTRY = {}
date_time_func = {
    "format_date": lambda data=None: datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
}
