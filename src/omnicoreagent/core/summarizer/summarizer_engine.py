"""
Summarizer engine for context engineering management.

Provides history preparation functions for both sliding window and token budget modes
with optional LLM-based summarization of older messages.
"""

from typing import Callable, Any, Coroutine
from omnicoreagent.core.summarizer.tokenizer import (
    count_tokens,
    count_message_tokens,
    DEFAULT_SUMMARY_RATIO,
)
from omnicoreagent.core.summarizer.summarizer_types import (
    SummaryConfig,
    SummaryRetentionPolicy,
    format_summary_content,
)
from omnicoreagent.core.utils import logger


SummarizeFn = Callable[[list[dict[str, Any]]], Coroutine[Any, Any, str]]
SummarizeFnWithBudget = Callable[[list[dict[str, Any]], int], Coroutine[Any, Any, str]]


async def prepare_history_sliding_window(
    messages: list[dict[str, Any]],
    window_size: int,
    agent_name: str | None = None,
    summarize_fn: SummarizeFn | None = None,
    summary_config: SummaryConfig | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Prepare history using sliding window with optional summarization.

    Design invariant:
    - Final working memory has exactly N messages (or less if history <= N)
    - If history > N and summarization enabled:
        - Last N-1 messages stay raw
        - Everything before becomes ONE summary
        - Summary occupies the Nth slot

    Args:
        messages: List of message dictionaries
        window_size: Maximum number of messages to keep
        agent_name: Name of the agent
        summarize_fn: Async function to generate summary from messages
        summary_config: Configuration for summarization behavior

    Returns:
        tuple: (working_memory, summarized_message_ids)
            - working_memory: Messages to use for LLM context
            - summarized_message_ids: IDs of messages that were summarized
    """
    if summary_config is None:
        summary_config = SummaryConfig()

    if len(messages) <= window_size:
        return messages, []

    if not summary_config.enabled or summarize_fn is None:
        logger.debug(
            f"Sliding window: truncating {len(messages)} messages to {window_size}"
        )
        return messages[-window_size:], []

    raw_keep_count = window_size - 1
    raw_messages = messages[-raw_keep_count:]
    messages_to_summarize = messages[:-raw_keep_count]

    summarized_ids = [
        m.get("id") for m in messages_to_summarize if m.get("id")
    ]

    logger.debug(
        f"Sliding window: summarizing {len(messages_to_summarize)} messages, "
        f"keeping {len(raw_messages)} recent"
    )

    try:
        summary_text = await summarize_fn(messages_to_summarize)
        summary_content = format_summary_content(summary_text)
    except Exception as e:
        logger.error(f"Summarization failed: {e}. Falling back to truncation.")
        return messages[-window_size:], []

    summary_message = {
        "role": "user",
        "content": summary_content,
        "msg_metadata": {
            "type": "history_summary",
            "summarizes": summarized_ids,
            "agent_name": agent_name if agent_name else "Unknown Agent",
        }
    }

    working_memory = [summary_message] + raw_messages

    assert len(working_memory) == window_size, (
        f"Expected {window_size} messages, got {len(working_memory)}"
    )

    return working_memory, summarized_ids


async def prepare_history_token_budget(
    messages: list[dict[str, Any]],
    max_tokens: int,
    agent_name: str | None = None,
    summarize_fn: SummarizeFnWithBudget | None = None,
    summary_config: SummaryConfig | None = None,
    model: str = "gpt-4",
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Prepare history using token budget with optional summarization.

    Design invariant:
    - Final working memory tokens <= max_tokens
    - Summary has a reserved budget (DEFAULT_SUMMARY_RATIO of max_tokens)
    - Raw messages fill the rest

    Args:
        messages: List of message dictionaries
        max_tokens: Maximum token budget for history
        agent_name: Name of the agent
        summarize_fn: Async function to generate summary (receives messages and max_tokens)
        summary_config: Configuration for summarization behavior
        model: Model name for token counting

    Returns:
        tuple: (working_memory, summarized_message_ids)
    """
    if summary_config is None:
        summary_config = SummaryConfig()

    total_tokens = count_message_tokens(messages, model)

    if total_tokens <= max_tokens:
        return messages, []

    if not summary_config.enabled or summarize_fn is None:
        logger.debug(
            f"Token budget: truncating from {total_tokens} to {max_tokens} tokens"
        )
        return _truncate_to_token_budget(messages, max_tokens, model), []

    summary_budget = int(max_tokens * DEFAULT_SUMMARY_RATIO)
    raw_budget = max_tokens - summary_budget

    raw_messages = []
    raw_tokens = 0

    for msg in reversed(messages):
        msg_tokens = count_tokens(str(msg.get("content", "")), model)
        if raw_tokens + msg_tokens > raw_budget:
            break
        raw_messages.insert(0, msg)
        raw_tokens += msg_tokens

    if raw_messages:
        messages_to_summarize = messages[:-len(raw_messages)]
    else:
        messages_to_summarize = messages

    summarized_ids = [
        m.get("id") for m in messages_to_summarize if m.get("id")
    ]

    logger.debug(
        f"Token budget: summarizing {len(messages_to_summarize)} messages "
        f"({count_message_tokens(messages_to_summarize, model)} tokens), "
        f"keeping {len(raw_messages)} recent ({raw_tokens} tokens)"
    )

    try:
        summary_text = await summarize_fn(messages_to_summarize, summary_budget)
        summary_content = format_summary_content(summary_text)
    except Exception as e:
        logger.error(f"Summarization failed: {e}. Falling back to truncation.")
        return _truncate_to_token_budget(messages, max_tokens, model), []

    summary_tokens = count_tokens(summary_content, model)
    if summary_tokens > summary_budget:
        logger.warning(
            f"Summary exceeded budget: {summary_tokens} > {summary_budget} tokens"
        )

    summary_message = {
        "role": "user",
        "content": summary_content,
        "msg_metadata": {
            "type": "history_summary",
            "summarizes": summarized_ids,
            "agent_name": agent_name if agent_name else "Unknown Agent",
        }
    }

    working_memory = [summary_message] + raw_messages

    final_tokens = count_message_tokens(working_memory, model)
    if final_tokens > max_tokens:
        logger.warning(
            f"Final token count {final_tokens} exceeds budget {max_tokens}"
        )

    return working_memory, summarized_ids


def _truncate_to_token_budget(
    messages: list[dict[str, Any]],
    max_tokens: int,
    model: str = "gpt-4",
) -> list[dict[str, Any]]:
    """
    Truncate messages from front to fit within token budget.

    Args:
        messages: List of messages
        max_tokens: Maximum token budget
        model: Model name for token counting

    Returns:
        list: Truncated messages fitting within budget
    """
    result = []
    current_tokens = 0

    for msg in reversed(messages):
        msg_tokens = count_tokens(str(msg.get("content", "")), model)
        if current_tokens + msg_tokens <= max_tokens:
            result.insert(0, msg)
            current_tokens += msg_tokens
        else:
            break

    return result


async def apply_summarization_logic(
    messages: list[dict[str, Any]],
    memory_config: dict[str, Any],
    summary_config: SummaryConfig | None,
    summarize_fn: Callable | None,
    agent_name: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    """
    Common orchestration logic for applying summarization strategies.

    Args:
        messages: The list of active messages to potentially summarize.
        memory_config: The memory configuration dict (mode, value).
        summary_config: The summary configuration object.
        summarize_fn: The callback to generate summaries.
        agent_name: The name of the agent.

    Returns:
        tuple containing:
        - working_memory: The list of messages to return (context).
        - summary_to_store: A dict of the new summary message if created, else None.
        - summarized_ids: List of message IDs that were summarized.
    """
    mode = memory_config.get("mode", "token_budget")
    value = memory_config.get("value")
    
    if value is None:
        return messages, None, []
        
    if not (summary_config and summary_config.enabled and summarize_fn):
        if mode.lower() == "sliding_window":
            return messages[-value:], None, []
        elif mode.lower() == "token_budget":
            return _truncate_to_token_budget(messages, value), None, []
        return messages, None, []

    summarized_ids = []
    working_memory = messages
    
    if mode.lower() == "sliding_window":
        working_memory, summarized_ids = await prepare_history_sliding_window(
            messages=messages,
            window_size=value,
            agent_name=agent_name,
            summarize_fn=summarize_fn,
            summary_config=summary_config,
        )
    elif mode.lower() == "token_budget":
        working_memory, summarized_ids = await prepare_history_token_budget(
            messages=messages,
            max_tokens=value,
            agent_name=agent_name,
            summarize_fn=summarize_fn,
            summary_config=summary_config,
        )
    
    summary_to_store = None
    if summarized_ids:
        potential_summary = working_memory[0] if working_memory else None
        if (
            potential_summary 
            and potential_summary.get("msg_metadata", {}).get("type") == "history_summary"
        ):
            summary_to_store = potential_summary
    return working_memory, summary_to_store, summarized_ids
