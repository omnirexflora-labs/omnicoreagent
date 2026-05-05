import logging
import inspect
import os
import random
import time
import warnings
from typing import Any

import litellm
import openai
from decouple import config as decouple_config
from dotenv import load_dotenv

from omnicoreagent.core.utils import logger

warnings.filterwarnings(
    "ignore", message="Pydantic serializer warnings", module="pydantic.main"
)

load_dotenv()

os.environ["LITELLM_LOG"] = "CRITICAL"
litellm.set_verbose = False
litellm.callbacks = []
litellm.success_callback = []
litellm.failure_callback = []

for logger_name in ["LiteLLM", "litellm", "litellm.proxy"]:
    _litellm_logger = logging.getLogger(logger_name)
    _litellm_logger.disabled = True
    _litellm_logger.setLevel(logging.CRITICAL)
    _litellm_logger.propagate = False


def retry_with_backoff(max_retries=3, base_delay=1, max_delay=60, backoff_factor=2):
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if not _is_retryable(e):
                        logger.error(f"Non-retryable error: {e}")
                        break
                    if attempt >= max_retries:
                        logger.error(
                            f"Max retries ({max_retries}) exceeded. Last error: {e}"
                        )
                        break
                    _sleep_before_retry(e, attempt, max_retries, base_delay, max_delay, backoff_factor)
            raise last_exception

        def sync_wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if not _is_retryable(e):
                        logger.error(f"Non-retryable error: {e}")
                        break
                    if attempt >= max_retries:
                        logger.error(
                            f"Max retries ({max_retries}) exceeded. Last error: {e}"
                        )
                        break
                    _sleep_before_retry(e, attempt, max_retries, base_delay, max_delay, backoff_factor)
            raise last_exception

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator


def _is_retryable(exc: Exception) -> bool:
    error_msg = str(exc).lower()
    return any(
        keyword in error_msg
        for keyword in [
            "rate limit",
            "rate_limit",
            "rpm",
            "tpm",
            "quota",
            "throttle",
            "too many requests",
            "429",
            "temporary",
            "timeout",
            "connection",
        ]
    )


def _sleep_before_retry(
    exc: Exception,
    attempt: int,
    max_retries: int,
    base_delay: int,
    max_delay: int,
    backoff_factor: int,
) -> None:
    delay = min(base_delay * (backoff_factor**attempt), max_delay)
    jitter = random.uniform(0, 0.1 * delay)
    total_delay = delay + jitter
    logger.warning(
        f"Retryable error on attempt {attempt + 1}/{max_retries + 1}: {exc}"
    )
    logger.info(f"Retrying in {total_delay:.2f} seconds...")
    time.sleep(total_delay)


class LLMConnection:
    """Direct LiteLLM connection configured from the agent runtime."""

    def __init__(self, model_config: dict[str, Any], api_key: str | None = None):
        self.model_config = dict(model_config or {})
        self.llm_api_key = api_key or self.model_config.get("api_key")
        self.llm_config = self._build_llm_config()
        self._set_llm_environment_variables()

    def __str__(self):
        model = self.llm_config.get("model") if self.llm_config else "unconfigured"
        return f"LLMConnection(model={model})"

    def __repr__(self):
        return self.__str__()

    def _build_llm_config(self) -> dict[str, Any]:
        provider = self.model_config.get("provider")
        model = self.model_config.get("model")
        if not provider or not model:
            raise ValueError("model_config requires provider and model")

        self.llm_api_key = self.llm_api_key or decouple_config("LLM_API_KEY", default=None)
        if not self.llm_api_key and provider.lower() != "ollama":
            raise ValueError("LLM_API_KEY not found in environment variables")

        provider_model_map = {
            "cencori": model,
            "openai": f"openai/{model}",
            "anthropic": f"anthropic/{model}",
            "groq": f"groq/{model}",
            "openrouter": f"openrouter/{model}",
            "deepseek": f"deepseek/{model}",
            "gemini": f"gemini/{model}",
            "azure": f"azure/{model}",
            "azureopenai": f"azure/{model}",
            "ollama": f"ollama/{model}",
            "mistral": f"mistral/{model}",
        }

        provider_key = provider.lower() if isinstance(provider, str) else ""
        full_model = provider_model_map.get(provider_key, model)

        if provider_key in {"azure", "azureopenai"}:
            azure_endpoint = self.model_config.get("azure_endpoint")
            azure_api_version = self.model_config.get("azure_api_version")
            azure_deployment = self.model_config.get("azure_deployment")
            if azure_endpoint:
                os.environ["AZURE_API_BASE"] = azure_endpoint
            if azure_api_version:
                os.environ["AZURE_API_VERSION"] = azure_api_version
            if azure_deployment:
                full_model = f"azure/{azure_deployment}"

        if provider_key == "ollama" and self.model_config.get("ollama_host"):
            os.environ["OLLAMA_API_BASE"] = self.model_config["ollama_host"]

        return {
            "provider": provider,
            "model": full_model,
            "temperature": self.model_config.get("temperature"),
            "max_tokens": self.model_config.get("max_tokens"),
            "top_p": self.model_config.get("top_p"),
        }

    def _set_llm_environment_variables(self):
        provider = self.llm_config["provider"].lower()
        env_names = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "groq": "GROQ_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "azure": "AZURE_API_KEY",
            "azureopenai": "AZURE_API_KEY",
            "cencori": "CENCORI_API_KEY",
        }
        env_name = env_names.get(provider)
        if env_name and self.llm_api_key:
            os.environ[env_name] = self.llm_api_key

    def is_llm_available(self) -> bool:
        return self.llm_api_key is not None or self.llm_config["provider"].lower() == "ollama"

    def to_dict(self, msg):
        if hasattr(msg, "model_dump"):
            msg_dict = msg.model_dump(exclude_none=True)
            if "timestamp" in msg_dict and hasattr(msg_dict["timestamp"], "timestamp"):
                msg_dict["timestamp"] = msg_dict["timestamp"].timestamp()
            return msg_dict
        if isinstance(msg, dict):
            return msg
        if hasattr(msg, "__dict__"):
            return {k: v for k, v in msg.__dict__.items() if v is not None}
        return msg

    @retry_with_backoff(max_retries=3, base_delay=1, max_delay=30)
    async def llm_call(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] = None,
    ):
        try:
            params = self._completion_params(messages, tools)
            if self.llm_config["provider"].lower() == "cencori":
                client = openai.AsyncOpenAI(
                    base_url="https://api.cencori.com/v1",
                    api_key=self.llm_api_key,
                )
                return await client.chat.completions.create(**params)
            litellm.drop_params = True
            return await litellm.acompletion(**params)
        except Exception as e:
            error_message = (
                f"Error calling LLM with model {self.llm_config.get('model')}: {e}"
            )
            logger.error(error_message)
            return None

    @retry_with_backoff(max_retries=3, base_delay=1, max_delay=30)
    def llm_call_sync(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] = None,
    ):
        try:
            params = self._completion_params(messages, tools)
            if self.llm_config["provider"].lower() == "cencori":
                client = openai.OpenAI(
                    base_url="https://api.cencori.com/v1",
                    api_key=self.llm_api_key,
                )
                return client.chat.completions.create(**params)
            litellm.drop_params = True
            return litellm.completion(**params)
        except Exception as e:
            error_message = (
                f"Error calling LLM with model {self.llm_config.get('model')}: {e}"
            )
            logger.error(error_message)
            return None

    def _completion_params(
        self, messages: list[Any], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        params = {
            "model": self.llm_config["model"],
            "messages": [self.to_dict(m) for m in messages],
        }

        for key in ("temperature", "max_tokens", "top_p"):
            if self.llm_config.get(key) is not None:
                params[key] = self.llm_config[key]

        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        if self.llm_config["provider"].lower() == "openrouter" and not tools:
            params["stop"] = ["\n\nObservation:"]

        return params
