"""Detect non-progress across complete native tool rounds."""

from collections import deque
from dataclasses import dataclass
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class ToolInteraction:
    provider: str
    server: str | None
    name: str
    arguments: Any
    result: Any

    def signature(self) -> str:
        value = [self.provider, self.server, self.name, self.arguments, self.result]
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()


class NativeLoopDetector:
    """A batch is one round, regardless of call count or completion order.

    Compare whole rounds so an unchanged helper cannot hide progress from a
    sibling. IDs, assistant prose and offload references are not evidence of
    progress. Repeated cycles of up to five rounds are detected too.
    """

    def __init__(self, repetitions: int = 5, max_pattern_length: int = 5):
        if repetitions < 2 or max_pattern_length < 1:
            raise ValueError(
                "Loop detection requires at least two repetitions and one round"
            )
        self.repetitions = repetitions
        self.max_pattern_length = max_pattern_length
        self.rounds = deque(maxlen=repetitions * max_pattern_length)

    def record_round(self, interactions: list[ToolInteraction]) -> None:
        if interactions:
            # A sorted multiset preserves duplicate-call counts but ignores
            # provider order and task scheduling within an independent batch.
            self.rounds.append(tuple(sorted(item.signature() for item in interactions)))

    def reset(self) -> None:
        self.rounds.clear()

    def is_looping(self) -> bool:
        rounds = list(self.rounds)
        for size in range(
            1, min(self.max_pattern_length, len(rounds) // self.repetitions) + 1
        ):
            pattern = rounds[-size:]
            if rounds[-size * self.repetitions :] == pattern * self.repetitions:
                return True
        return False
