import hashlib
from collections import defaultdict, deque

from omnicoreagent.core.logging import logger


def hash_text(text: str) -> str:
    """Generate a stable hash for loop-detection signatures."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RobustLoopDetector:
    """Detect repeated identical or patterned tool interactions."""

    def __init__(
        self,
        maxlen: int = 20,
        consecutive_threshold: int = 7,
        pattern_detection: bool = True,
        max_pattern_length: int = 5,
        pattern_repetition_threshold: int = 4,
        debug: bool = True,
    ):
        self.global_interactions = deque(maxlen=maxlen)
        self.tool_interactions: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=maxlen)
        )
        self.consecutive_threshold = max(1, consecutive_threshold)
        self.pattern_detection = pattern_detection
        self.max_pattern_length = max(1, max_pattern_length)
        self.pattern_repetition_threshold = max(4, pattern_repetition_threshold)
        self._last_signature = None
        self._consecutive_count = 0
        self.debug = debug

    def record_tool_call(
        self, tool_name: str, tool_input: str, tool_output: str
    ) -> None:
        tool_name = tool_name or "unknown_tool"
        tool_input = tool_input if tool_input is not None else ""
        tool_output = tool_output if tool_output is not None else ""
        signature = (
            tool_name,
            hash_text(tool_input),
            hash_text(tool_output),
        )

        self.global_interactions.append(signature)
        self.tool_interactions[tool_name].append(signature)

        if signature == self._last_signature:
            self._consecutive_count += 1
        else:
            self._last_signature = signature
            self._consecutive_count = 1

        if self.debug:
            logger.info(
                "[LoopDetector] Tool '%s' called. Consecutive count: %s "
                "(signature: %s, input_hash=%s..., output_hash=%s...)",
                tool_name,
                self._consecutive_count,
                tool_name,
                signature[1][:8],
                signature[2][:8],
            )

    def reset(self, tool_name: str | None = None) -> None:
        if tool_name and tool_name.strip():
            self.tool_interactions.pop(tool_name, None)
            if self._last_signature and self._last_signature[0] == tool_name:
                self._last_signature = None
                self._consecutive_count = 0
        else:
            self.global_interactions.clear()
            self.tool_interactions.clear()
            self._last_signature = None
            self._consecutive_count = 0

        if self.debug:
            reset_target = (
                f"tool '{tool_name}'"
                if tool_name and tool_name.strip()
                else "all tools"
            )
            logger.info("[LoopDetector] Reset performed for %s.", reset_target)

    def _is_tool_stuck_consecutive(self, tool_name: str) -> bool:
        if not tool_name:
            return False

        tool_history = self.tool_interactions.get(tool_name, [])
        if not tool_history or self._last_signature is None:
            return False

        stuck = (
            tool_history[-1] == self._last_signature
            and self._consecutive_count >= self.consecutive_threshold
        )
        if self.debug and stuck:
            logger.info(
                "[LoopDetector] Tool '%s' is stuck due to %s consecutive identical calls.",
                tool_name,
                self._consecutive_count,
            )
        return stuck

    def _has_tool_pattern_loop(self, tool_name: str) -> bool:
        if not tool_name or not self.pattern_detection:
            return False

        interactions = list(self.tool_interactions.get(tool_name, []))
        if len(interactions) < 4:
            return False

        max_checkable_pattern = min(
            self.max_pattern_length,
            len(interactions) // (self.pattern_repetition_threshold + 1),
        )
        if max_checkable_pattern < 1:
            return False

        for pattern_len in range(1, max_checkable_pattern + 1):
            required_length = pattern_len * (self.pattern_repetition_threshold + 1)
            if len(interactions) < required_length:
                continue

            pattern = interactions[-pattern_len:]
            is_loop = True
            for i in range(1, self.pattern_repetition_threshold + 1):
                start_idx = -(i + 1) * pattern_len
                end_idx = -i * pattern_len
                prev_pattern = interactions[start_idx:end_idx]
                if len(prev_pattern) != pattern_len or prev_pattern != pattern:
                    is_loop = False
                    break

            if is_loop:
                if self.debug:
                    logger.info(
                        "[LoopDetector] Tool '%s' has repeating pattern: %s steps "
                        "repeated %s times.",
                        tool_name,
                        pattern_len,
                        self.pattern_repetition_threshold + 1,
                    )
                return True

        return False

    def is_looping(self, tool_name: str | None = None) -> bool:
        if tool_name is not None:
            if not tool_name or not tool_name.strip():
                return False
            return self._is_tool_stuck_consecutive(
                tool_name
            ) or self._has_tool_pattern_loop(tool_name)

        if not self.tool_interactions:
            return False
        return any(
            self._is_tool_stuck_consecutive(name) or self._has_tool_pattern_loop(name)
            for name in self.tool_interactions
        )

    def get_loop_type(self, tool_name: str | None = None) -> list[str]:
        types = []

        if tool_name is not None:
            if not tool_name or not tool_name.strip():
                return types
            if self._is_tool_stuck_consecutive(tool_name):
                types.append("consecutive_calls")
            if self._has_tool_pattern_loop(tool_name):
                types.append("repeating_pattern")
            return types

        if not self.tool_interactions:
            return types

        for name in self.tool_interactions:
            if self._is_tool_stuck_consecutive(name):
                types.append(f"{name}: consecutive_calls")
            if self._has_tool_pattern_loop(name):
                types.append(f"{name}: repeating_pattern")
        return types
