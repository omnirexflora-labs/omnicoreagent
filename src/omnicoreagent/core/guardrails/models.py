from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ThreatLevel(Enum):
    SAFE = "safe"
    LOW_RISK = "low_risk"
    SUSPICIOUS = "suspicious"
    DANGEROUS = "dangerous"
    CRITICAL = "critical"


@dataclass
class DetectionConfig:
    """Configuration for detection parameters"""

    strict_mode: bool = False
    sensitivity: float = 1.0
    enable_ml_fallback: bool = False
    max_input_length: int = 10000
    enable_encoding_detection: bool = True
    enable_heuristic_analysis: bool = True
    enable_sequential_analysis: bool = True
    enable_entropy_analysis: bool = True
    log_level: str = "INFO"
    allowlist_patterns: list[str] = field(default_factory=list)
    blocklist_patterns: list[str] = field(default_factory=list)


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
