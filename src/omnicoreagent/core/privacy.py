"""Privacy boundaries for data that leaves the active model context.

The model still receives the task content it needs by default.  Persistence,
telemetry, workspace artifacts, stream events, and public results use this
shared filter so a caller does not have to remember a separate redaction rule
at every output seam.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import re
from typing import Any


PII_CATEGORIES = frozenset({"email", "phone", "ssn", "credit_card"})
PRIVACY_BOUNDARIES = frozenset(
    {"telemetry", "memory", "workspace", "stream", "public", "model"}
)


@dataclass
class PrivacyConfig:
    """Configurable privacy policy for persisted and externally visible data."""

    enabled: bool = True
    redact_telemetry: bool = True
    redact_memory: bool = True
    redact_workspace: bool = True
    redact_stream: bool = True
    redact_public: bool = True
    redact_model_io: bool = False
    categories: list[str] = field(default_factory=lambda: sorted(PII_CATEGORIES))

    def __post_init__(self) -> None:
        for name in (
            "enabled",
            "redact_telemetry",
            "redact_memory",
            "redact_workspace",
            "redact_stream",
            "redact_public",
            "redact_model_io",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a bool")
        if not isinstance(self.categories, list):
            raise ValueError("categories must be a list of PII category names")
        normalized = []
        for index, category in enumerate(self.categories):
            if not isinstance(category, str) or not category.strip():
                raise ValueError(f"categories[{index}] must be a non-empty string")
            category = category.strip().lower()
            if category not in PII_CATEGORIES:
                allowed = ", ".join(sorted(PII_CATEGORIES))
                raise ValueError(
                    f"categories[{index}] must be one of: {allowed}"
                )
            if category not in normalized:
                normalized.append(category)
        self.categories = normalized

    @classmethod
    def from_value(cls, value: "PrivacyConfig | dict[str, Any] | None") -> "PrivacyConfig":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(**value)
        raise TypeError("privacy_config must be a PrivacyConfig, dict, or None")

    def fingerprint(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


class PrivacyFilter:
    """Redact configured PII recursively at a named data boundary."""

    _EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
    _SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    _CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
    _PHONE = re.compile(r"(?<!\w)\+?\d[\d().\-\s]{8,}\d(?!\w)")

    _MARKERS = {
        "email": "[REDACTED_EMAIL]",
        "phone": "[REDACTED_PHONE]",
        "ssn": "[REDACTED_SSN]",
        "credit_card": "[REDACTED_CREDIT_CARD]",
    }

    def __init__(self, config: PrivacyConfig | dict[str, Any] | None = None):
        self.config = PrivacyConfig.from_value(config)

    @classmethod
    def from_value(cls, value: "PrivacyFilter | PrivacyConfig | dict[str, Any] | None") -> "PrivacyFilter":
        if isinstance(value, cls):
            return value
        return cls(value)

    def redact(self, value: Any, *, boundary: str) -> Any:
        """Return a recursively redacted value for the requested boundary."""
        if boundary not in PRIVACY_BOUNDARIES:
            raise ValueError(f"Unknown privacy boundary: {boundary}")
        if not self.config.enabled or not self._enabled_for(boundary):
            return value
        if isinstance(value, dict):
            return {
                key: self.redact(item, boundary=boundary) for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.redact(item, boundary=boundary) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact(item, boundary=boundary) for item in value)
        if isinstance(value, str):
            return self.redact_text(value, boundary=boundary)
        return value

    def redact_text(self, value: str, *, boundary: str) -> str:
        if boundary not in PRIVACY_BOUNDARIES:
            raise ValueError(f"Unknown privacy boundary: {boundary}")
        if not self.config.enabled or not self._enabled_for(boundary):
            return value
        redacted = value
        for category in ("email", "ssn", "credit_card", "phone"):
            if category not in self.config.categories:
                continue
            pattern = self._CARD if category == "credit_card" else getattr(
                self, f"_{category.upper()}"
            )
            if category == "credit_card":
                redacted = pattern.sub(self._replace_card, redacted)
            elif category == "phone":
                redacted = pattern.sub(self._replace_phone, redacted)
            else:
                redacted = pattern.sub(self._MARKERS[category], redacted)
        return redacted

    def _enabled_for(self, boundary: str) -> bool:
        if boundary == "model":
            return self.config.redact_model_io
        return bool(getattr(self.config, f"redact_{boundary}"))

    def _replace_card(self, match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group())
        return self._MARKERS["credit_card"] if self._luhn_valid(digits) else match.group()

    def _replace_phone(self, match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group())
        return self._MARKERS["phone"] if len(digits) >= 10 else match.group()

    @staticmethod
    def _luhn_valid(digits: str) -> bool:
        if not 13 <= len(digits) <= 19:
            return False
        total = 0
        parity = len(digits) % 2
        for index, char in enumerate(digits):
            digit = int(char)
            if index % 2 == parity:
                digit *= 2
                if digit > 9:
                    digit -= 9
            total += digit
        return total % 10 == 0


__all__ = [
    "PII_CATEGORIES",
    "PRIVACY_BOUNDARIES",
    "PrivacyConfig",
    "PrivacyFilter",
]
