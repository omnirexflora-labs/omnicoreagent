from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import logging
import math
import re
from numbers import Real
from typing import Any


class ThreatLevel(Enum):
    SAFE = "safe"
    LOW_RISK = "low_risk"
    SUSPICIOUS = "suspicious"
    DANGEROUS = "dangerous"
    CRITICAL = "critical"


SUSPICIOUS_OUTPUT_ACTIONS = frozenset({"block", "flag"})


@dataclass
class DetectionConfig:
    """Configuration for detection parameters"""

    strict_mode: bool = False
    sensitivity: float = 1.0
    max_input_length: int = 10000
    enable_encoding_detection: bool = True
    enable_heuristic_analysis: bool = True
    enable_sequential_analysis: bool = True
    enable_entropy_analysis: bool = True
    log_level: str = "INFO"
    allowlist_patterns: list[str] = field(default_factory=list)
    blocklist_patterns: list[str] = field(default_factory=list)
    # What to do with tool output that scores "suspicious" (dangerous and
    # critical output is always blocked): "flag" records it and passes it
    # through; "block" replaces it. Flag is the default — code, test names
    # and documentation are full of the words the score counts, and a
    # blocked tool result stops ordinary work; found by the repository
    # steward running pytest.
    suspicious_output_action: str = "flag"

    def __post_init__(self) -> None:
        """Validate the policy before an engine can consume it.

        Detection settings are security policy.  Letting malformed values reach
        the analysis loop either weakens scoring (for example, a non-positive
        sensitivity) or turns a bad regular expression into a runtime
        ``analysis_error`` result.  Fail at construction instead so callers can
        repair configuration before accepting user or tool content.
        """
        self._validate_bool("strict_mode", self.strict_mode)
        if isinstance(self.sensitivity, bool) or not isinstance(self.sensitivity, Real):
            raise ValueError("sensitivity must be a finite number greater than 0")
        self.sensitivity = float(self.sensitivity)
        if not math.isfinite(self.sensitivity) or self.sensitivity <= 0:
            raise ValueError("sensitivity must be a finite number greater than 0")

        self._validate_positive_int("max_input_length", self.max_input_length)
        for name in (
            "enable_encoding_detection",
            "enable_heuristic_analysis",
            "enable_sequential_analysis",
            "enable_entropy_analysis",
        ):
            self._validate_bool(name, getattr(self, name))

        if not isinstance(self.log_level, str):
            raise ValueError("log_level must be a standard logging level name")
        normalized_level = self.log_level.strip().upper()
        valid_levels = logging.getLevelNamesMapping()
        if normalized_level not in valid_levels or not isinstance(
            valid_levels[normalized_level], int
        ):
            allowed = ", ".join(sorted(valid_levels))
            raise ValueError(f"log_level must be one of: {allowed}")
        self.log_level = normalized_level

        self.allowlist_patterns = self._validate_patterns(
            "allowlist_patterns", self.allowlist_patterns
        )
        self.blocklist_patterns = self._validate_patterns(
            "blocklist_patterns", self.blocklist_patterns
        )
        if not isinstance(self.suspicious_output_action, str):
            raise ValueError(
                "suspicious_output_action must be one of: block, flag"
            )
        self.suspicious_output_action = self.suspicious_output_action.strip().lower()
        if self.suspicious_output_action not in SUSPICIOUS_OUTPUT_ACTIONS:
            allowed = ", ".join(sorted(SUSPICIOUS_OUTPUT_ACTIONS))
            raise ValueError(
                f"suspicious_output_action must be one of: {allowed}"
            )

    @staticmethod
    def _validate_bool(name: str, value: Any) -> None:
        if type(value) is not bool:
            raise ValueError(f"{name} must be a bool")

    @staticmethod
    def _validate_positive_int(name: str, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")

    @staticmethod
    def _validate_patterns(name: str, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError(f"{name} must be a list of regular expression strings")
        validated: list[str] = []
        for index, pattern in enumerate(value):
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"{name}[{index}] must be a non-empty string")
            try:
                re.compile(pattern, re.IGNORECASE | re.MULTILINE | re.UNICODE)
            except re.error as exc:
                raise ValueError(f"{name}[{index}] is not a valid regex: {exc}") from exc
            validated.append(pattern)
        return validated

    def fingerprint(self) -> str:
        """Return a stable, non-secret identifier for the effective policy."""
        encoded = json.dumps(
            {
                key: value
                for key, value in self.__dict__.items()
                if key not in {"allowlist_patterns", "blocklist_patterns"}
            }
            | {
                "allowlist_patterns": list(self.allowlist_patterns),
                "blocklist_patterns": list(self.blocklist_patterns),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


@dataclass
class DetectionResult:
    """Structured detection result"""

    threat_level: ThreatLevel
    is_safe: bool
    flags: list[str]
    confidence: float
    threat_score: int
    message: str
    recommendations: list[str]
    input_length: int
    input_hash: str
    detection_time: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization"""
        result = {
            "threat_level": self.threat_level.value,
            "is_safe": self.is_safe,
            "flags": self.flags,
            "confidence": self.confidence,
            "threat_score": self.threat_score,
            "message": self.message,
            "recommendations": self.recommendations,
            "input_length": self.input_length,
            "input_hash": self.input_hash,
            "detection_time": self.detection_time.isoformat(),
        }
        result.update(self.metadata)
        return result

    def to_json(self) -> str:
        """Serialize to JSON"""
        return json.dumps(self.to_dict())
