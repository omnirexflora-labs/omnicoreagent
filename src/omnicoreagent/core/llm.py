import asyncio
import logging
import inspect
from copy import deepcopy
import os
import random
import re
import time
import warnings
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from omnicoreagent.core.logging import logger

warnings.filterwarnings(
    "ignore", message="Pydantic serializer warnings", module="pydantic.main"
)

_LITELLM_CONFIGURED = False

for logger_name in ["LiteLLM", "litellm", "litellm.proxy"]:
    _litellm_logger = logging.getLogger(logger_name)
    _litellm_logger.setLevel(logging.CRITICAL)
    _litellm_logger.propagate = False


def _get_litellm():
    global _LITELLM_CONFIGURED

    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    if not _LITELLM_CONFIGURED:
        os.environ["LITELLM_LOG"] = "CRITICAL"
        litellm.set_verbose = False
        # Its "Give Feedback / Get Help" banner on every error reads, to a new
        # user, as the place to report this runtime's errors.
        litellm.suppress_debug_info = True
        litellm.telemetry = False
        litellm.callbacks = []
        litellm.success_callback = []
        litellm.failure_callback = []
        _LITELLM_CONFIGURED = True

    return litellm


# "<model> doesn't support temperature=0.2 while reasoning is active"
_REFUSED_PARAMETER = re.compile(r"doesn't support ([a-z_]+)=")


def _notify_unsupported(name: str, error: Exception) -> None:
    observer = MODEL_RETRY_OBSERVER.get()
    logger.warning(f"The model refused {name}; retrying the call without it: {error}")
    if observer is not None:
        observer(
            {
                "attempt": 1,
                "error_type": error.__class__.__name__,
                # Every retry record says what happened under the same name,
                # so a reader never has to know which kind it is holding.
                "message": str(error)[:300],
                "reason": "unsupported_parameter",
                "dropped": [name],
            }
        )


# Receives one record per retried provider failure, so telemetry can show
# every attempt of a model call rather than only the final outcome.
MODEL_RETRY_OBSERVER: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "omnicoreagent_model_retry_observer", default=None
)


def _notify_retry(attempt: int, error: Exception, delay: float) -> None:
    observer = MODEL_RETRY_OBSERVER.get()
    if observer is None:
        return
    try:
        observer(
            {
                "attempt": attempt,
                "error_type": error.__class__.__name__,
                "message": str(error),
                "delay_seconds": delay,
            }
        )
    except Exception:
        logger.debug("Model retry observer failed", exc_info=True)


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
                    delay = _retry_delay(
                        e,
                        attempt,
                        max_retries,
                        base_delay,
                        max_delay,
                        backoff_factor,
                    )
                    _notify_retry(attempt + 1, e, delay)
                    await asyncio.sleep(delay)
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
                    _sleep_before_retry(
                        e, attempt, max_retries, base_delay, max_delay, backoff_factor
                    )
            raise last_exception

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator


# An account that cannot pay or authenticate stays that way however long one
# waits. Providers send some of these as 429s, which read as rate limits: the
# steward's account ran out of credits and every call was retried four times.
_ACCOUNT_ERRORS = {
    "insufficient_quota": "the provider account has no credits left (insufficient_quota)",
    "credit_balance_exhausted": "the provider account has no credits left (insufficient_quota)",
    "billing_hard_limit_reached": "the provider account reached its billing limit",
    "invalid_api_key": "the provider rejected the API key (invalid_api_key)",
    "incorrect api key": "the provider rejected the API key (invalid_api_key)",
    "authenticationerror": "the provider rejected the API key",
    "permission_denied": "the provider account is not allowed to use this model",
}


def account_error(exc: BaseException) -> str | None:
    """What is wrong with the provider account, when that is the error."""
    text = str(exc).lower()
    return next((reason for marker, reason in _ACCOUNT_ERRORS.items() if marker in text), None)


_MODEL_NAME = re.compile(r"model [`'\"]?([\w.:/@-]+)[`'\"]? does not exist", re.IGNORECASE)


def model_error(exc: BaseException) -> str | None:
    """What is wrong with the model name, when the provider does not serve it."""
    text = str(exc)
    lowered = text.lower()
    if "does not exist or you do not have access" not in lowered and "model_not_found" not in lowered:
        return None
    found = _MODEL_NAME.search(text)
    name = f" `{found.group(1)}`" if found else ""
    return (
        f"the provider does not serve the model{name} to this account; check "
        "model_config's model name and provider"
    )


def _is_retryable(exc: Exception) -> bool:
    if account_error(exc) is not None or model_error(exc) is not None:
        return False
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


def _retry_delay(
    exc: Exception,
    attempt: int,
    max_retries: int,
    base_delay: int,
    max_delay: int,
    backoff_factor: int,
) -> float:
    delay = min(base_delay * (backoff_factor**attempt), max_delay)
    jitter = random.uniform(0, 0.1 * delay)
    total_delay = delay + jitter
    logger.warning(f"Retryable error on attempt {attempt + 1}/{max_retries + 1}: {exc}")
    logger.info(f"Retrying in {total_delay:.2f} seconds...")
    return total_delay


def _sleep_before_retry(*args):
    time.sleep(_retry_delay(*args))


# Continuation data each provider's LiteLLM path reads back from the assistant
# message. LiteLLM sends unknown fields to OpenAI-compatible providers as they
# are, so each field goes only to the provider that uses it.
CONTINUATION_FIELDS_BY_PROVIDER = {
    "anthropic": frozenset({"thinking_blocks"}),
    "gemini": frozenset({"provider_specific_fields"}),
}
# Providers that read continuation data from each tool call.
TOOL_CALL_FIELDS_PROVIDERS = frozenset({"gemini"})


# Generation settings sent as given, and recorded with the call.
# ``logprobs``/``top_logprobs`` are what a trainer needs to reuse a run
# (traces for training plan, R3); a provider that refuses one names it, and
# it is dropped for the retry like any other setting.
_MODEL_SETTINGS = (
    "temperature",
    "max_tokens",
    "top_p",
    "reasoning_effort",
    "logprobs",
    "top_logprobs",
)


class LLMConnection:
    """Provider connection through LiteLLM."""

    def __init__(self, model_config: dict[str, Any], api_key: str | None = None):
        self.model_config = dict(model_config or {})
        self._warmed = False
        # Parameters this model has refused by name; not sent again.
        self._unsupported_params: set[str] = set()
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

        self.llm_api_key = self.llm_api_key or os.environ.get("LLM_API_KEY")
        if not self.llm_api_key and provider.lower() != "ollama":
            raise ValueError("LLM_API_KEY not found in environment variables")

        provider_model_map = {
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
        if provider_key not in provider_model_map:
            raise ValueError(f"Unsupported provider: {provider}")
        full_model = provider_model_map[provider_key]

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
            "logprobs": self.model_config.get("logprobs"),
            "top_logprobs": self.model_config.get("top_logprobs"),
            "reasoning_effort": self.model_config.get("reasoning_effort"),
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
        }
        env_name = env_names.get(provider)
        if env_name and self.llm_api_key:
            os.environ[env_name] = self.llm_api_key

    def is_llm_available(self) -> bool:
        return (
            self.llm_api_key is not None
            or self.llm_config["provider"].lower() == "ollama"
        )

    def to_dict(self, msg):
        """Serialize model-facing fields only; persistence metadata stays internal."""
        if hasattr(msg, "model_dump"):
            msg = msg.model_dump(exclude_none=True)
        elif not isinstance(msg, dict) and hasattr(msg, "__dict__"):
            msg = vars(msg)
        if not isinstance(msg, dict):
            raise TypeError("Model messages must be mappings or message records")
        allowed = {
            "role",
            "content",
            "name",
            "tool_calls",
            "tool_call_id",
            "refusal",
            "reasoning_content",
        }
        provider = str(self.llm_config.get("provider", "")).lower()
        allowed |= CONTINUATION_FIELDS_BY_PROVIDER.get(provider, frozenset())
        sent = {key: deepcopy(value) for key, value in msg.items() if key in allowed}

        if provider == "openrouter":
            # LiteLLM keeps OpenRouter's reasoning details inside
            # provider_specific_fields but sends them back only from the top level.
            details = (msg.get("provider_specific_fields") or {}).get("reasoning_details")
            if details:
                sent["reasoning_details"] = deepcopy(details)
        if sent.get("tool_calls") and provider not in TOOL_CALL_FIELDS_PROVIDERS:
            # Per-call provider fields (Gemini's thought signature) stay with the
            # provider that issued them.
            for call in sent["tool_calls"]:
                call.pop("provider_specific_fields", None)
                if isinstance(call.get("function"), dict):
                    call["function"].pop("provider_specific_fields", None)
        return sent

    @retry_with_backoff(max_retries=3, base_delay=1, max_delay=30)
    async def llm_call(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] = None,
    ):
        try:
            params = self._completion_params(messages, tools)
            litellm = _get_litellm()
            params.update(api_key=self.llm_api_key, drop_params=False, num_retries=0)
            try:
                return await litellm.acompletion(**params)
            except Exception as refused:
                dropped = self._refused_parameter(refused, params)
                if dropped is None:
                    raise
                # The provider named the parameter it refuses. Parameters are
                # sent as given on purpose (a silent drop hides a mistake), so
                # the retry is recorded with the name, and not sent again.
                _notify_unsupported(dropped, refused)
                return await litellm.acompletion(**params)
        except Exception as e:
            error_message = (
                f"Error calling LLM with model {self.llm_config.get('model')}: {e}"
            )
            logger.error(error_message)
            raise

    def _refused_parameter(self, error: Exception, params: dict[str, Any]) -> str | None:
        """The parameter a provider's refusal names, removed from ``params`` and
        remembered, or None when the error is not that kind."""
        if type(error).__name__ != "UnsupportedParamsError":
            return None
        match = _REFUSED_PARAMETER.search(str(error))
        if match is None:
            return None
        name = match.group(1)
        if name not in params:
            return None
        params.pop(name, None)
        self._unsupported_params.add(name)
        return name

    @retry_with_backoff(max_retries=3, base_delay=1, max_delay=30)
    def llm_call_sync(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] = None,
    ):
        try:
            params = self._completion_params(messages, tools)
            litellm = _get_litellm()
            params.update(api_key=self.llm_api_key, drop_params=False, num_retries=0)
            return litellm.completion(**params)
        except Exception as e:
            error_message = (
                f"Error calling LLM with model {self.llm_config.get('model')}: {e}"
            )
            logger.error(error_message)
            raise

    async def llm_stream(self, messages, tools=None):
        """Yield text deltas followed by one complete normalized turn.

        No automatic retry: replaying a partially observed stream would duplicate
        output. The caller receives errors and cancellation directly.
        """
        from omnicoreagent.core.model_stream import ModelStreamAssembler

        params = self._completion_params(messages, tools)
        params.update(stream=True, stream_options={"include_usage": True})
        assembler = ModelStreamAssembler()
        stream = None
        try:
            litellm = _get_litellm()
            params.update(api_key=self.llm_api_key, drop_params=False, num_retries=0)
            stream = await litellm.acompletion(**params)
            async for chunk in stream:
                for event in assembler.feed(chunk):
                    yield event
            yield {"type": "turn_complete", "turn": assembler.finish()}
        finally:
            if stream is not None:
                close = getattr(stream, "aclose", None) or getattr(
                    stream, "close", None
                )
                if close is not None:
                    result = close()
                    if inspect.isawaitable(result):
                        await result

    async def warm_up(self) -> None:
        """Load the provider client now, off the event loop, so no request has to.

        ``import litellm`` costs seconds of CPU. It is imported lazily so that
        building an agent stays light, which means the first request of a
        process would otherwise pay for it. A server calls this while it
        starts. A failure is left for the request that needs the client, which
        reports it properly; this only tries early.
        """
        if self._warmed:
            return
        self._warmed = True
        try:
            await asyncio.to_thread(_get_litellm)
        except Exception as exc:
            self._warmed = False
            logger.debug(f"The model client could not be loaded early: {exc}")

    def request_settings(self) -> dict[str, Any]:
        """The model and generation settings sent with every request."""
        settings = {"model": self.llm_config["model"]}
        for key in _MODEL_SETTINGS:
            if self.llm_config.get(key) is not None:
                settings[key] = self.llm_config[key]
        return settings

    def estimate_cost(self, usage: Any) -> float | None:
        """Price a call's token usage from LiteLLM's model price table.

        Used when the provider response carries no cost (streamed calls).
        Returns ``None`` when the model has no known price.
        """
        prompt_tokens = getattr(usage, "request_tokens", None)
        completion_tokens = getattr(usage, "response_tokens", None)
        if prompt_tokens is None or completion_tokens is None:
            return None
        details = getattr(usage, "details", None) or {}
        try:
            # Cached input is billed at a lower rate; ignoring it overstated
            # the cost of a cached call about twice over.
            prompt_cost, completion_cost = _get_litellm().cost_per_token(
                model=self.llm_config["model"],
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cache_read_input_tokens=details.get("cached_input_tokens", 0),
            )
        except Exception:
            return None
        return float(prompt_cost + completion_cost)

    def _completion_params(
        self, messages: list[Any], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        params = {
            "model": self.llm_config["model"],
            "messages": [self.to_dict(m) for m in messages],
        }

        for key in _MODEL_SETTINGS:
            if self.llm_config.get(key) is not None and key not in self._unsupported_params:
                params[key] = self.llm_config[key]

        if tools:
            params["tools"] = tools

        if self.llm_config["provider"].lower() == "openai" and "max_tokens" in params:
            params["max_completion_tokens"] = params.pop("max_tokens")
        return params
