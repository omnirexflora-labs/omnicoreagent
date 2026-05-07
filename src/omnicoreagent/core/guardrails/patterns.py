from __future__ import annotations

import logging
import re
from typing import Any


class PatternManager:
    """Manages attack patterns with versioning and updates"""

    def __init__(self):
        self.pattern_version = "1.2.0"
        self._compiled_patterns = {}
        self._load_patterns()

    def _load_patterns(self):
        """Load and compile all patterns"""
        self.patterns = {
            "instruction_override": {
                "weight": 12,
                "requires_target": True,
                "patterns": [
                    (
                        r"\b(?:ignore|disregard|forget|override|bypass|skip|cancel|break)\s+(?:all|any|previous|prior|above|earlier|your|the|existing|current)\s+(?:instructions?|rules?|prompts?|commands?|directives?|guidelines?|constraints?|safeguards?)",
                        True,
                    ),
                    (
                        r"\b(?:new|updated|revised|latest|current|different|alternate|secret|hidden)\s+(?:instructions?|commands?|directives?|rules?|prompt|system)\s*[:=]\s*(?=\w)",
                        True,
                    ),
                    (
                        r"(?:system|assistant|model|ai)\s*[:=]\s*(?:ignore|disregard|override|bypass|break|remove)",
                        True,
                    ),
                    (
                        r"\b(?:clear|erase|delete|remove|reset|wipe)\s+(?:all\s+)?(?:previous|prior|existing|current|original)\s+(?:instructions?|context|memory|rules?|prompt|system)",
                        True,
                    ),
                    (
                        r"from\s+now\s+on\s*,\s*(?:you are|you're|you will be)\s*(?:no longer\s+)?(?:bound|constrained|restricted|limited)",
                        True,
                    ),
                ],
            },
            "prompt_extraction": {
                "weight": 10,
                "requires_target": True,
                "patterns": [
                    (
                        r"\b(?:show|tell|give|reveal|display|print|output|repeat|echo|expose|disclose|leak|dump)\s+(?:me\s+)?(?:all\s+)?(?:your\s+|the\s+)?(?:exact\s+|full\s+|complete\s+|entire\s+)?(?:system\s+|hidden\s+|secret\s+|initial\s+|original\s+|base\s+|internal\s+|private\s+)?(?:prompt|instructions?|directives?|guidelines?|configuration|settings|rules?)",
                        True,
                    ),
                    (
                        r"what\s+(?:are|were|is|was)\s+(?:your|the)\s+(?:exact\s+|full\s+|complete\s+)?(?:initial|original|system|base|hidden|secret|full|complete|internal)\s+(?:prompt|instructions?|config|settings)",
                        True,
                    ),
                    (
                        r"(?:print|dump|export|output|write)\s+(?:your\s+|the\s+)?(?:system\s+)?(?:prompt|instructions?|config|settings|file)",
                        True,
                    ),
                    (
                        r"repeat\s+(?:everything\s+)?(?:you\s+were\s+told|your\s+instructions?|back\s+to\s+me|word\s+for\s+word)",
                        True,
                    ),
                    (
                        r"what\s+(?:are|were)\s+you\s+(?:originally\s+|initially\s+)?(?:told|instructed|programmed)",
                        True,
                    ),
                ],
            },
            "jailbreak_roleplay": {
                "weight": 9,
                "requires_target": False,
                "patterns": [
                    (
                        r"\b(?:enter|enable|activate|switch\s+to|go\s+into|turn\s+on|initiate)\s+(?:the\s+)?(?:developer|admin|god|sudo|debug|root|super|unrestricted|unsafe|privileged|advanced)\s+(?:mode|state|protocol)",
                        True,
                    ),
                    (
                        r"\b(?:DAN|STAN|MONG|EVIE|UCAR)\s*(?:v?\d+\.?\d*)?\s*(?:mode|protocol)?\s*(?:activate|enabled|on)",
                        True,
                    ),
                    (
                        r"\b(?:jailbreak|break\s+free|escape|become\s+(?:unfiltered|unrestricted|uncensored|unlimited|free)|remove\s+(?:all\s+)?restrictions)",
                        True,
                    ),
                    (
                        r"you\s+(?:are\s+now|have\s+become|will\s+be|must\s+be)\s+(?:an?\s+)?(?:unrestricted|unfiltered|uncensored|unlimited|free|rogue)\s+(?:AI|assistant|bot|model|entity)",
                        True,
                    ),
                    (
                        r"from\s+now\s+on\s*,?\s*you\s+(?:will\s+)?(?:not\s+)?(?:have|follow|obey|respect|adhere\s+to)\s+(?:any\s+)?(?:rules?|restrictions?|limitations?|guidelines?|policies?|safeguards?)",
                        True,
                    ),
                    (
                        r"(?:pretend|imagine|assume|act\s+as\s+if)\s+you\s+are\s+(?:an?\s+)?(?:unrestricted|unfiltered|hacked|compromised)",
                        True,
                    ),
                ],
            },
            "delimiter_injection": {
                "weight": 11,
                "requires_target": False,
                "patterns": [
                    (
                        r"<[/\s]*(?:system|instruction|prompt|admin|root|user_role|assistant_role|command|directive)[^>]*>",
                        False,
                    ),
                    (
                        r"\[\s*[/\s]*(?:system|instruction|prompt|admin|root|command)[^\]]*\]",
                        False,
                    ),
                    (
                        r"```+\s*(?:system|instruction|prompt|config|admin|root|command|directive)",
                        False,
                    ),
                    (
                        r"(?:---|===|\*\*\*)\s*(?:end|start|begin|new|reset)\s+(?:of\s+)?(?:system|instructions?|prompt|context|conversation)",
                        False,
                    ),
                    (
                        r"(?:system|prompt|instructions?)\s*(?:start|begin|end|terminate)\s*:",
                        False,
                    ),
                ],
            },
            "context_manipulation": {
                "weight": 8,
                "requires_target": False,
                "patterns": [
                    (
                        r"\b(?:end|stop|terminate|close|finish|halt|pause)\s+(?:of\s+)?(?:system|context|instructions?|prompt|conversation)\s*\.?\s*(?:now|here|immediately)?\s*(?:start|begin|new|initiate|resume)",
                        False,
                    ),
                    (
                        r"this\s+(?:message|input|text|prompt|query|request)\s+(?:overrides?|replaces?|cancels?|supersedes?|invalidates?)\s+(?:all|everything|previous|prior|above)",
                        True,
                    ),
                    (
                        r"(?:CRITICAL|URGENT|EMERGENCY|PRIORITY|IMPORTANT|VITAL)\s*(?:INSTRUCTION|COMMAND|DIRECTIVE|MESSAGE|ALERT)\s*[:=]\s*(?:ignore|override|disregard|bypass)",
                        True,
                    ),
                    (
                        r"highest\s+(?:priority|importance)\s+(?:instruction|command|directive|request)",
                        True,
                    ),
                    (
                        r"treat\s+this\s+as\s+(?:the\s+)?(?:only|main|primary|real)\s+(?:instruction|command|prompt)",
                        True,
                    ),
                ],
            },
            "payload_encoding": {
                "weight": 7,
                "requires_target": False,
                "patterns": [
                    (r"(?:\\x[0-9a-f]{2,}|%[0-9a-f]{2}|&#x?[0-9a-f]+;)", False),
                    (r"\\u[0-9a-f]{4,}", False),
                    (
                        r"\b(?:base64|rot13|rot-?13|hex|unicode|url|binary)\s*(?:encode|decode|decrypt|encrypt)\s*[:=\(]",
                        False,
                    ),
                    (r"[0-9a-f]{8,}", False),
                    (
                        r"(?:[A-Za-z0-9+/]{4}){4,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?",
                        False,
                    ),
                ],
            },
            "obfuscation_techniques": {
                "weight": 6,
                "requires_target": False,
                "patterns": [
                    (
                        r"\b(?:s\s*e\s*c\s*r\s*e\s*t|i\s*n\s*j\s*e\s*c\s*t|o\s*v\s*e\s*r\s*r\s*i\s*d\s*e)",
                        False,
                    ),
                    (r"([^\[\]{}()\"',:\n])\1{3,}", False),
                    (r"[^\w\s{}\[\]():,\"'.=/\\-]{4,}", False),
                    (
                        r"\b[a-zA-Z]*\d[a-zA-Z]+\d[a-zA-Z]*\d[a-zA-Z]*\b",
                        False,
                    ),
                ],
            },
        }

        for group_name, config in self.patterns.items():
            compiled_patterns = []
            for pattern_str, is_strict in config["patterns"]:
                try:
                    compiled = re.compile(
                        pattern_str, re.IGNORECASE | re.MULTILINE | re.UNICODE
                    )
                    compiled_patterns.append((compiled, is_strict))
                except re.error as e:
                    logging.warning(f"Failed to compile pattern {pattern_str}: {e}")
            config["patterns"] = compiled_patterns

    def get_patterns(self) -> dict[str, dict[str, Any]]:
        """Get all compiled patterns"""
        return self.patterns

    def add_pattern(
        self, group: str, pattern: str, weight: int = 5, requires_target: bool = False
    ):
        """Add a new pattern at runtime"""
        if group not in self.patterns:
            self.patterns[group] = {
                "weight": weight,
                "requires_target": requires_target,
                "patterns": [],
            }

        try:
            compiled = re.compile(pattern, re.IGNORECASE | re.MULTILINE | re.UNICODE)
            self.patterns[group]["patterns"].append((compiled, requires_target))
        except re.error as e:
            logging.error(f"Failed to add pattern {pattern}: {e}")
