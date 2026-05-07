from __future__ import annotations

from collections import defaultdict
from typing import Any

from omnicoreagent.core.guardrails.engine import DetectionEngine
from omnicoreagent.core.guardrails.models import DetectionConfig, DetectionResult


class PromptInjectionGuard:
    """
    Production-ready prompt injection guardrail.

    Features:
    - Multi-stage detection pipeline
    - Advanced normalization and obfuscation detection
    - Context-aware pattern matching
    - Heuristic and entropy analysis
    - Sequential pattern detection
    - Comprehensive logging and monitoring
    - Extensible pattern management
    - Structured results with recommendations
    """

    def __init__(self, config: DetectionConfig | None = None):
        self.config = config or DetectionConfig()
        self.detection_engine = DetectionEngine(self.config)
        self.detection_stats = defaultdict(int)

    def check(self, user_input: str) -> DetectionResult:
        """
        Analyze input for prompt injection attempts.

        Args:
            user_input: The text to analyze

        Returns:
            DetectionResult: Structured analysis result
        """
        result = self.detection_engine.analyze(user_input)

        self.detection_stats[result.threat_level.value] += 1
        self.detection_stats["total_checks"] += 1

        return result

    def check_batch(self, inputs: list[str]) -> list[DetectionResult]:
        """Analyze multiple inputs"""
        return [self.check(input_text) for input_text in inputs]

    def get_stats(self) -> dict[str, Any]:
        """Get detection statistics"""
        return {
            "total_checks": self.detection_stats["total_checks"],
            "safe_count": self.detection_stats.get("safe", 0),
            "low_risk_count": self.detection_stats.get("low_risk", 0),
            "suspicious_count": self.detection_stats.get("suspicious", 0),
            "dangerous_count": self.detection_stats.get("dangerous", 0),
            "critical_count": self.detection_stats.get("critical", 0),
        }

    def update_config(self, **kwargs):
        """Update configuration"""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

    def add_custom_pattern(self, group: str, pattern: str, **kwargs):
        """Add custom detection pattern"""
        self.detection_engine.pattern_manager.add_pattern(group, pattern, **kwargs)


def create_guard(
    strict: bool = False, sensitivity: float = 1.0, **kwargs
) -> PromptInjectionGuard:
    """Factory function to create a guard instance"""
    config = DetectionConfig(strict_mode=strict, sensitivity=sensitivity, **kwargs)
    return PromptInjectionGuard(config)


def quick_check(user_input: str, strict: bool = False) -> dict[str, Any]:
    """Quick one-off check for prompt injection"""
    guard = create_guard(strict=strict)
    result = guard.check(user_input)
    return result.to_dict()
