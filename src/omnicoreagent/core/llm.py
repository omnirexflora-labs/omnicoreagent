import os
from typing import Any
import time
import random

import openai

from dotenv import load_dotenv
import litellm
from omnicoreagent.core.utils import logger
import warnings

warnings.filterwarnings(
    "ignore", message="Pydantic serializer warnings", module="pydantic.main"
)


load_dotenv()


import logging

os.environ["LITELLM_LOG"] = "CRITICAL"

litellm.set_verbose = False

litellm.callbacks = []
litellm.success_callback = []
litellm.failure_callback = []

logging.getLogger("LiteLLM").disabled = True
logging.getLogger("litellm").disabled = True
logging.getLogger("litellm.proxy").disabled = True

for logger_name in ["LiteLLM", "litellm", "litellm.proxy"]:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.CRITICAL)
    logger.propagate = False


def retry_with_backoff(max_retries=3, base_delay=1, max_delay=60, backoff_factor=2):
    """Retry decorator with exponential backoff and jitter.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        backoff_factor: Multiplier for delay increase
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except Exception as e:
                    last_exception = e
                    error_msg = str(e).lower()

                    if any(
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
                    ):
                        if attempt < max_retries:
                            delay = min(
                                base_delay * (backoff_factor**attempt), max_delay
                            )
                            jitter = random.uniform(0, 0.1 * delay)
                            total_delay = delay + jitter

                            logger.warning(
                                f"Retryable error on attempt {attempt + 1}/{max_retries + 1}: {e}"
                            )
                            logger.info(f"Retrying in {total_delay:.2f} seconds...")

                            time.sleep(total_delay)
                            continue
                        else:
                            logger.error(
                                f"Max retries ({max_retries}) exceeded. Last error: {e}"
                            )
                    else:
                        logger.error(f"Non-retryable error: {e}")
                        break

            raise last_exception

        return wrapper

    return decorator


class LLMConnection:
    """Manages LLM connections using LiteLLM."""

    def __init__(self, config: dict[str, Any] | Any, config_filename: str):
        self.config = config
        self.config_filename = config_filename
        self.llm_config = None

        if hasattr(self.config, "llm_api_key"):
            if not self.llm_config:
                logger.info("updating llm configuration")
                llm_config_result = self.llm_configuration()
                if llm_config_result:
                    logger.info(f"LLM configuration: {self.llm_config}")

                    self._set_llm_environment_variables()
                else:
                    logger.debug("LLM configuration not available or invalid")
        else:
            logger.debug("Config object doesn't have llm_api_key, skipping LLM setup")

    def __str__(self):
        """Return a readable string representation of the LLMConnection."""
        config_file = self.config_filename or "default"
        return f"LLMConnection(config={config_file})"

    def __repr__(self):
        """Return a detailed representation of the LLMConnection."""
        return self.__str__()

    def get_loaded_config(self):
        """Get the already-loaded configuration without reloading it"""
        if not hasattr(self, "_loaded_config"):
            if self.config is None:
                raise ValueError("Config object is None - cannot load configuration")
            self._loaded_config = self.config.load_config(self.config_filename)
        return self._loaded_config

    def llm_configuration(self):
        """Load the LLM configuration"""

        config = self.get_loaded_config()

        if "LLM" not in config:
            logger.debug("No LLM configuration found in config file")
            return None

        llm_config = config["LLM"]
        try:
            provider = llm_config.get("provider")
            model = llm_config.get("model")

            if not provider or not model:
                logger.warning(
                    "LLM configuration missing required fields (provider or model)"
                )
                return None

            provider_model_map = {
                "cencori": f"{model}",
                "openai": f"openai/{model}",
                "anthropic": f"anthropic/{model}",
                "groq": f"groq/{model}",
                "openrouter": f"openrouter/{model}",
                "deepseek": f"deepseek/{model}",
                "gemini": f"gemini/{model}",
                "azure": f"azure/{model}",
                "ollama": f"ollama/{model}",
                "mistral": f"mistral/{model}",
            }

            provider_key = (
                provider.lower() if provider and isinstance(provider, str) else ""
            )
            full_model = provider_model_map.get(provider_key, model)

            self.llm_config = {
                "provider": provider,
                "model": full_model,
                "temperature": llm_config.get("temperature"),
                "max_tokens": llm_config.get("max_tokens"),
                "top_p": llm_config.get("top_p"),
            }

            if (
                provider
                and isinstance(provider, str)
                and provider.lower() == "azureopenai"
            ):
                azure_endpoint = llm_config.get("azure_endpoint")
                azure_api_version = llm_config.get("azure_api_version")
                azure_deployment = llm_config.get("azure_deployment")

                if azure_endpoint and isinstance(azure_endpoint, str):
                    os.environ["AZURE_API_BASE"] = azure_endpoint
                if azure_api_version and isinstance(azure_api_version, str):
                    os.environ["AZURE_API_VERSION"] = azure_api_version
                if azure_deployment and isinstance(azure_deployment, str):
                    self.llm_config["model"] = f"azure/{azure_deployment}"

            if provider and isinstance(provider, str) and provider.lower() == "ollama":
                ollama_host = llm_config.get("ollama_host")
                if ollama_host and isinstance(ollama_host, str):
                    os.environ["OLLAMA_API_BASE"] = ollama_host

            return self.llm_config
        except Exception as e:
            logger.error(f"Error loading LLM configuration: {e}")
            return None

    def _set_llm_environment_variables(self):
        """Set environment variables only for the configured LLM provider."""
        if not self.llm_config or not self.llm_config.get("provider"):
            return

        provider = self.llm_config["provider"].lower()
        api_key = self.config.llm_api_key

        if provider == "openai":
            os.environ["OPENAI_API_KEY"] = api_key
        elif provider == "anthropic":
            os.environ["ANTHROPIC_API_KEY"] = api_key
        elif provider == "groq":
            os.environ["GROQ_API_KEY"] = api_key
        elif provider == "mistral":
            os.environ["MISTRAL_API_KEY"] = api_key
        elif provider == "gemini":
            os.environ["GEMINI_API_KEY"] = api_key
        elif provider == "deepseek":
            os.environ["DEEPSEEK_API_KEY"] = api_key
        elif provider == "openrouter":
            os.environ["OPENROUTER_API_KEY"] = api_key
        elif provider == "azure" or provider == "azureopenai":
            os.environ["AZURE_API_KEY"] = api_key
        elif provider == "cencori":
            os.environ["CENCORI_API_KEY"] = api_key

        logger.debug(f"Set environment variable for LLM provider: {provider}")

    def is_llm_available(self) -> bool:
        """Check if LLM functionality is available (API key is set)"""
        return (
            hasattr(self.config, "llm_api_key") and self.config.llm_api_key is not None
        )

    def to_dict(self, msg):
        if hasattr(msg, "model_dump"):
            msg_dict = msg.model_dump(exclude_none=True)

            if "timestamp" in msg_dict and hasattr(msg_dict["timestamp"], "timestamp"):
                msg_dict["timestamp"] = msg_dict["timestamp"].timestamp()
            elif "timestamp" in msg_dict and hasattr(msg_dict["timestamp"], "tzinfo"):
                msg_dict["timestamp"] = msg_dict["timestamp"].timestamp()
            return msg_dict
        elif isinstance(msg, dict):
            return msg
        elif hasattr(msg, "__dict__"):
            return {k: v for k, v in msg.__dict__.items() if v is not None}
        else:
            return msg

    @retry_with_backoff(max_retries=3, base_delay=1, max_delay=30)
    async def llm_call(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] = None,
    ):
        """Call the LLM using LiteLLM"""
        try:
            if not self.llm_config:
                logger.debug("LLM configuration not loaded, skipping LLM call")
                return None

            messages_dicts = [self.to_dict(m) for m in messages]

            params = {
                "model": self.llm_config["model"],
                "messages": messages_dicts,
            }

            if self.llm_config.get("temperature") is not None:
                params["temperature"] = self.llm_config["temperature"]

            if self.llm_config.get("max_tokens") is not None:
                params["max_tokens"] = self.llm_config["max_tokens"]

            if self.llm_config.get("top_p") is not None:
                params["top_p"] = self.llm_config["top_p"]

            if tools:
                params["tools"] = tools
                params["tool_choice"] = "auto"

            if self.llm_config["provider"].lower() == "openrouter":
                if not tools:
                    params["stop"] = ["\n\nObservation:"]

            litellm.drop_params = True

            if self.llm_config["provider"].lower() == "cencori":
                client = openai.AsyncOpenAI(
                    base_url="https://api.cencori.com/v1",
                    api_key=self.config.llm_api_key,
                )

                model_name = self.llm_config["model"]

                kwargs = {
                    "model": model_name,
                    "messages": messages_dicts,
                }
                if self.llm_config.get("temperature") is not None:
                    kwargs["temperature"] = self.llm_config.get("temperature")
                if self.llm_config.get("max_tokens") is not None:
                    kwargs["max_tokens"] = self.llm_config.get("max_tokens")
                if self.llm_config.get("top_p") is not None:
                    kwargs["top_p"] = self.llm_config.get("top_p")
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"

                response = await client.chat.completions.create(**kwargs)
                return response

            response = await litellm.acompletion(**params)
            return response

        except Exception as e:
            error_message = (
                f"Error calling LLM with model {self.llm_config.get('model')}: {e}"
            )
            print(error_message)
            logger.error(error_message)
            return None

    @retry_with_backoff(max_retries=3, base_delay=1, max_delay=30)
    def llm_call_sync(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] = None,
    ):
        """Synchronous call to the LLM using LiteLLM"""
        try:
            if not self.llm_config:
                logger.debug("LLM configuration not loaded, skipping LLM call")
                return None

            messages_dicts = [self.to_dict(m) for m in messages]

            params = {
                "model": self.llm_config["model"],
                "messages": messages_dicts,
            }

            if self.llm_config.get("temperature") is not None:
                params["temperature"] = self.llm_config["temperature"]

            if self.llm_config.get("max_tokens") is not None:
                params["max_tokens"] = self.llm_config["max_tokens"]

            if self.llm_config.get("top_p") is not None:
                params["top_p"] = self.llm_config["top_p"]

            if tools:
                params["tools"] = tools
                params["tool_choice"] = "auto"

            if self.llm_config["provider"].lower() == "openrouter":
                if not tools:
                    params["stop"] = ["\n\nObservation:"]

            litellm.drop_params = True

            if self.llm_config["provider"].lower() == "cencori":
                client = openai.OpenAI(
                    base_url="https://api.cencori.com/v1",
                    api_key=self.config.llm_api_key,
                )

                model_name = self.llm_config["model"]

                kwargs = {
                    "model": model_name,
                    "messages": messages_dicts,
                }
                if self.llm_config.get("temperature") is not None:
                    kwargs["temperature"] = self.llm_config.get("temperature")
                if self.llm_config.get("max_tokens") is not None:
                    kwargs["max_tokens"] = self.llm_config.get("max_tokens")
                if self.llm_config.get("top_p") is not None:
                    kwargs["top_p"] = self.llm_config.get("top_p")
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"

                response = client.chat.completions.create(**kwargs)
                return response

            response = litellm.completion(**params)
            return response

        except Exception as e:
            error_message = (
                f"Error calling LLM with model {self.llm_config.get('model')}: {e}"
            )
            logger.error(error_message)
            return None
