"""
Agent Loop Context Manager

Manages context within a single agent.run() execution to prevent token exhaustion
during long-running tasks. This is Layer 2 of our context management strategy.

Layer 1: Session Memory (MemoryRouter) - manages across agent.run() calls
Layer 2: Agent Loop Context (this module) - manages within a single agent.run()
"""

from typing import Any, Callable, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from omnicoreagent.core.summarizer.tokenizer import count_tokens, count_message_tokens
from omnicoreagent.core.utils import logger


class ContextManagementMode(str, Enum):
    """Mode for context management."""
    SLIDING_WINDOW = "sliding_window"
    TOKEN_BUDGET = "token_budget"


class ContextManagementStrategy(str, Enum):
    """Strategy for handling context overflow."""
    TRUNCATE = "truncate"
    SUMMARIZE_AND_TRUNCATE = "summarize_and_truncate"


@dataclass
class ContextManagementConfig:
    """Configuration for agent loop context management."""
    enabled: bool = False
    mode: ContextManagementMode = ContextManagementMode.TOKEN_BUDGET
    value: int = 100000
    threshold_percent: int = 75
    strategy: ContextManagementStrategy = ContextManagementStrategy.TRUNCATE
    preserve_recent: int = 4
    
    @classmethod
    def from_dict(cls, config: dict) -> "ContextManagementConfig":
        """Create config from dictionary."""
        if not config:
            return cls()
        
        return cls(
            enabled=config.get("enabled", False),
            mode=ContextManagementMode(config.get("mode", "token_budget")),
            value=config.get("value", 100000),
            threshold_percent=config.get("threshold_percent", 75),
            strategy=ContextManagementStrategy(config.get("strategy", "truncate")),
            preserve_recent=config.get("preserve_recent", 4),
        )


class AgentLoopContextManager:
    """
    Manages context within a single agent.run() execution.
    
    Prevents token exhaustion during long-running tasks by:
    1. Monitoring context size (tokens or message count)
    2. Triggering management when threshold is exceeded
    3. Applying truncation or summarization strategy
    
    System prompt is ALWAYS preserved (first message with role="system").
    """
    
    def __init__(self, config: ContextManagementConfig | dict = None):
        if isinstance(config, dict):
            config = ContextManagementConfig.from_dict(config)
        self.config = config or ContextManagementConfig()
        
        self._management_count = 0
        self._tokens_saved = 0
    
    def get_current_token_count(self, messages: List[Any]) -> int:
        """
        Count total tokens in all messages.
        Uses tiktoken for accurate counting (same as summarizer).
        
        Args:
            messages: List of Message objects or dicts
            
        Returns:
            Total token count across all messages
        """
        total = 0
        for msg in messages:
            if hasattr(msg, "content"):
                content = msg.content
            elif isinstance(msg, dict):
                content = msg.get("content", "")
            else:
                content = str(msg)
            
            total += count_tokens(content)
        
        return total
    
    def get_non_system_message_count(self, messages: List[Any]) -> int:
        """Count messages excluding system prompt."""
        count = 0
        for msg in messages:
            role = msg.role if hasattr(msg, "role") else msg.get("role", "")
            if role != "system":
                count += 1
        return count
    
    def should_trigger(self, messages: List[Any]) -> bool:
        """
        Check if context management should trigger.
        
        Args:
            messages: Current message list
            
        Returns:
            True if context management should be applied
        """
        if not self.config.enabled:
            return False
        
        if self.config.mode == ContextManagementMode.TOKEN_BUDGET:
            current_tokens = self.get_current_token_count(messages)
            logger.info(f"Current token count: {current_tokens}")
            threshold = self.config.value * (self.config.threshold_percent / 100)
            logger.info(f"Threshold: {threshold}")
            should_trigger = current_tokens > threshold
            logger.info(f"Should trigger: {should_trigger}")
            
            if should_trigger:
                logger.info(
                    f"Context management triggered: {current_tokens} tokens > "
                    f"{threshold:.0f} threshold ({self.config.threshold_percent}% of {self.config.value})"
                )
            
            return should_trigger
            
        elif self.config.mode == ContextManagementMode.SLIDING_WINDOW:
            message_count = self.get_non_system_message_count(messages)
            should_trigger = message_count > self.config.value
            
            if should_trigger:
                logger.info(
                    f"Context management triggered: {message_count} messages > "
                    f"{self.config.value} limit"
                )
            
            return should_trigger
        
        return False
    
    def _separate_messages(self, messages: List[Any]) -> tuple:
        """
        Separate messages into system, middle, and recent.
        
        Returns:
            (system_messages, middle_messages, recent_messages)
        """
        system_messages = []
        other_messages = []
        
        for msg in messages:
            role = msg.role if hasattr(msg, "role") else msg.get("role", "")
            if role == "system":
                system_messages.append(msg)
            else:
                other_messages.append(msg)
        
        preserve_count = self.config.preserve_recent
        if len(other_messages) <= preserve_count:
            return system_messages, [], other_messages
        
        recent_messages = other_messages[-preserve_count:]
        middle_messages = other_messages[:-preserve_count]
        
        return system_messages, middle_messages, recent_messages
    
    async def manage_context(
        self,
        messages: List[Any],
        summarize_fn: Optional[Callable] = None,
    ) -> List[Any]:
        """
        Apply context management to reduce message list size.
        
        Args:
            messages: Current message list
            summarize_fn: Optional async function to summarize messages
            
        Returns:
            Managed message list (reduced size)
        """
        if not self.config.enabled:
            return messages
        
        tokens_before = self.get_current_token_count(messages)
        
        system_msgs, middle_msgs, recent_msgs = self._separate_messages(messages)
        
        if not middle_msgs:
            return messages
        
        if self.config.strategy == ContextManagementStrategy.TRUNCATE:
            managed = system_msgs + recent_msgs
            
        elif self.config.strategy == ContextManagementStrategy.SUMMARIZE_AND_TRUNCATE:
            if summarize_fn:
                try:
                    summary_text = await summarize_fn(middle_msgs)
                    summary_msg = {
                        "role": "user",
                        "content": f"[CONTEXT SUMMARY]\n{summary_text}",
                        "msg_metadata": {"type": "context_summary", "summarized_count": len(middle_msgs)}
                    }
                    managed = system_msgs + [summary_msg] + recent_msgs
                except Exception as e:
                    logger.error(f"Context summarization failed: {e}, falling back to truncate")
                    managed = system_msgs + recent_msgs
            else:
                logger.warning("summarize_fn not provided, falling back to truncate strategy")
                managed = system_msgs + recent_msgs
        else:
            managed = messages
        
        tokens_after = self.get_current_token_count(managed)
        tokens_saved = tokens_before - tokens_after
        
        self._management_count += 1
        self._tokens_saved += tokens_saved
        
        logger.info(
            f"Context managed: {len(messages)} -> {len(managed)} messages, "
            f"{tokens_before} -> {tokens_after} tokens (saved {tokens_saved})"
        )
        
        return managed
    
    def get_stats(self) -> dict:
        """Get context management statistics."""
        return {
            "management_count": self._management_count,
            "tokens_saved": self._tokens_saved,
            "config": {
                "enabled": self.config.enabled,
                "mode": self.config.mode.value,
                "value": self.config.value,
                "threshold_percent": self.config.threshold_percent,
                "strategy": self.config.strategy.value,
                "preserve_recent": self.config.preserve_recent,
            }
        }
    
    def reset_stats(self):
        """Reset management statistics."""
        self._management_count = 0
        self._tokens_saved = 0
