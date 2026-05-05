from typing import Any

from dotenv import load_dotenv
from decouple import config as decouple_config
import litellm
from mcp.client.session import ClientSession
from mcp.shared.context import RequestContext
from mcp.types import (
    CreateMessageRequestParams,
    CreateMessageResult,
    ErrorData,
    TextContent,
)

from omnicoreagent.core.types import ContextInclusion
from omnicoreagent.core.utils import logger

load_dotenv()

api_key = decouple_config("LLM_API_KEY", default=None)


class LLMConnection:
    def __init__(self):
        self.llm_config = None

    async def llm_call(
        self,
        messages: list[dict[str, Any]],
        provider,
        model,
        temperature,
        max_tokens,
        stop,
    ):
        try:
            provider = provider.lower()

            provider_model_map = {
                "openai": f"openai/{model}",
                "anthropic": f"anthropic/{model}",
                "groq": f"groq/{model}",
                "gemini": f"gemini/{model}",
                "deepseek": f"deepseek/{model}",
                "openrouter": f"openrouter/{model}",
                "azureopenai": f"azure/{model}",
                "ollama": f"ollama/{model}",
            }

            full_model = provider_model_map.get(provider)

            def to_dict(msg):
                if hasattr(msg, "model_dump"):
                    return msg.model_dump(exclude_none=True)
                elif isinstance(msg, dict):
                    return msg
                elif hasattr(msg, "__dict__"):
                    return {k: v for k, v in msg.__dict__.items() if v is not None}
                else:
                    return msg

            messages_dicts = [to_dict(m) for m in messages]
            response = await litellm.acompletion(
                model=full_model,
                messages=messages_dicts,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
            )
            return response
        except Exception as e:
            logger.error(f"Error calling LLM for provider '{provider}': {e}")
            return ErrorData(
                code="INVALID_REQUEST",
                message=f"Unsupported LLM provider: {provider}",
            )
        except Exception as e:
            logger.error(f"Error calling LLM for provider '{provider}': {e}")
            return ErrorData(
                code="INTERNAL_ERROR", message=f"An error occurred: {str(e)}"
            )


class samplingCallback:
    def __init__(
        self,
        model_config: dict[str, Any] | None = None,
        api_key: str | None = None,
    ):
        self.model_config = dict(model_config or {})
        if api_key:
            self.model_config["api_key"] = api_key
        self.llm_connection = LLMConnection()

    async def load_model(self):
        if not self.model_config:
            return [], None
        models = self.model_config.get("model", [])
        if not isinstance(models, list):
            models = [models]
        provider = self.model_config.get("provider")
        provider = provider.lower() if isinstance(provider, str) else None
        available_models = list(models)
        return available_models, provider

    async def _select_model(self, preferences, available_models: list[str]) -> str:
        """Select the best model based on preferences and available models."""

        if not preferences or not preferences.hints:
            return available_models[0]

        for hint in preferences.hints:
            if not hint.name:
                continue
            for model in available_models:
                if hint.name.lower() in model.lower():
                    return model

        if preferences.intelligencePriority and preferences.intelligencePriority > 0.7:
            return max(available_models, key=lambda x: len(x))
        elif preferences.speedPriority and preferences.speedPriority > 0.7:
            return min(available_models, key=lambda x: len(x))
        elif preferences.costPriority and preferences.cosPriority > 0.7:
            return min(available_models, key=lambda x: len(x))

        return available_models[0]

    async def _get_context(
        self,
        include_context: ContextInclusion | None,
        server_name: str = None,
    ) -> str:
        """Get relevant context based on inclusion type."""
        if not include_context or include_context == ContextInclusion.NONE:
            return ""

        context_parts = []

        if include_context == ContextInclusion.THIS_SERVER:
            if server_name in self.sessions:
                session_data = self.sessions[server_name]
                if "message_history" in session_data:
                    context_parts.extend(session_data["message_history"])

        elif include_context == ContextInclusion.ALL_SERVERS:
            for session_data in self.sessions.values():
                if "message_history" in session_data:
                    context_parts.extend(session_data["message_history"])

        return "\n".join(context_parts)

    async def _sampling(
        self,
        context: RequestContext["ClientSession", Any],
        params: CreateMessageRequestParams,
    ) -> CreateMessageResult | ErrorData:
        """Enhanced sampling callback with support for advanced features."""
        try:
            if not params.messages or not isinstance(params.maxTokens, int):
                return ErrorData(
                    code="INVALID_REQUEST",
                    message="Missing required fields: messages or max_tokens",
                )

            available_models, provider = await self.load_model()
            if not available_models or not provider:
                return ErrorData(
                    code="INVALID_REQUEST",
                    message="No sampling model configured for this MCP client",
                )

            model = await self._select_model(params.modelPreferences, available_models)

            additional_context = await self._get_context(params.includeContext)

            messages = []
            if params.systemPrompt:
                messages.append({"role": "system", "content": params.systemPrompt})
            if additional_context:
                messages.append(
                    {
                        "role": "system",
                        "content": f"Context: {additional_context}",
                    }
                )
            messages.extend(
                [
                    {"role": msg.role, "content": msg.content.text}
                    for msg in params.messages
                ]
            )

            response = await self.llm_connection.llm_call(
                provider=provider,
                messages=messages,
                model=model,
                temperature=params.temperature,
                max_tokens=params.maxTokens,
                stop=params.stopSequences,
            )
            completion = response.choices[0].message.content
            stop_reason = response.choices[0].finish_reason

            result = CreateMessageResult(
                model=model,
                stop_reason=stop_reason,
                role="assistant",
                content=TextContent(type="text", text=completion),
            )

            logger.debug(f"Sampling callback completed successfully: {result}")
            return result

        except Exception as e:
            logger.error(f"Error in sampling callback: {str(e)}")
            return ErrorData(
                code="INTERNAL_ERROR", message=f"An error occurred: {str(e)}"
            )
