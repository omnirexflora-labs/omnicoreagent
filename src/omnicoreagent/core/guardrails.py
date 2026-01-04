import re
import logging
from typing import Dict, List, Tuple, Optional, Any
from enum import Enum
import unicodedata
import json
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from collections import defaultdict
import sys


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
    allowlist_patterns: List[str] = field(default_factory=list)
    blocklist_patterns: List[str] = field(default_factory=list)


@dataclass
class DetectionResult:
    """Structured detection result"""

    threat_level: ThreatLevel
    is_safe: bool
    flags: List[str]
    confidence: float
    threat_score: int
    message: str
    recommendations: List[str]
    input_length: int
    input_hash: str
    detection_time: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
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
                    (r"(.)\1{3,}", False),
                    (r"[^\w\s]{4,}", False),
                    (
                        r"\b\w*[\d_]\w*[\d_]\w*\b",
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

    def get_patterns(self) -> Dict:
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


class DetectionEngine:
    """Core detection engine with multiple analysis stages"""

    def __init__(self, config: DetectionConfig):
        self.config = config
        self.pattern_manager = PatternManager()
        self.logger = self._setup_logger()
        self._benign_patterns = self._compile_benign_patterns()

    def _setup_logger(self) -> logging.Logger:
        """Setup logging"""
        logger = logging.getLogger(f"PromptGuard_{id(self)}")
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        logger.setLevel(getattr(logging, self.config.log_level))
        return logger

    def _compile_benign_patterns(self) -> List[re.Pattern]:
        """Compile benign context patterns"""
        patterns = [
            r"help me (?:ignore|avoid|overcome|manage|deal with|handle)",
            r"how (?:do|can|could|should|would) I (?:ignore|avoid|prevent|stop)",
            r"teach(?:ing)? (?:me|us) to (?:ignore|disregard|avoid)",
            r"(?:show|tell|explain) me (?:how|why|what|when|where)",
            r"pretend (?:you're|you are) (?:a |an )?(?:teacher|tutor|guide|helper|expert|coach|assistant)",
            r"act as (?:a |an )?(?:teacher|tutor|guide|helper|expert|professional|assistant)",
            r"roleplay (?:a |an )?(?:lesson|scenario|conversation|dialogue|situation)",
            r"simulate (?:a |an )?(?:conversation|dialogue|interview|scenario|situation)",
            r"what are your (?:thoughts|views|opinions|capabilities|features|limitations)",
            r"explain your (?:reasoning|process|thinking|approach|method|logic)",
            r"can you (?:help|assist|guide) me with",
            r"I need (?:help|assistance|guidance) with",
            r"what is (?:the|your) (?:best|recommended) way to",
            r"how does (?:this|it|the system) work",
            r"for (?:educational|learning|training|research) purposes",
        ]

        compiled = []
        for pattern in patterns:
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error:
                continue
        return compiled

    def analyze(self, user_input: str) -> DetectionResult:
        """Main analysis pipeline"""
        start_time = datetime.now()

        try:
            if not isinstance(user_input, str):
                user_input = str(user_input)

            user_input = user_input.strip()
            input_hash = hashlib.sha256(user_input.encode("utf-8")).hexdigest()

            if not user_input:
                return self._create_safe_result(input_hash, 0, start_time)

            if len(user_input) > self.config.max_input_length:
                return self._create_result(
                    threat_level=ThreatLevel.SUSPICIOUS,
                    flags=["input_too_long"],
                    score=10,
                    message="Input exceeds maximum allowed length",
                    input_length=len(user_input),
                    input_hash=input_hash,
                    start_time=start_time,
                )

            if self.config.allowlist_patterns:
                if any(
                    re.search(p, user_input, re.IGNORECASE)
                    for p in self.config.allowlist_patterns
                ):
                    return self._create_safe_result(
                        input_hash, len(user_input), start_time
                    )

            if self.config.blocklist_patterns:
                for pattern in self.config.blocklist_patterns:
                    if re.search(pattern, user_input, re.IGNORECASE):
                        return self._create_result(
                            threat_level=ThreatLevel.DANGEROUS,
                            flags=[f"blocklist_match: {pattern[:50]}"],
                            score=20,
                            message="Input matches blocklist pattern",
                            input_length=len(user_input),
                            input_hash=input_hash,
                            start_time=start_time,
                        )

            normalized = self._normalize_input(user_input)

            if self._is_likely_benign(user_input, normalized):
                result = self._analyze_with_reduced_sensitivity(user_input, normalized)
                if result["threat_level"] in [ThreatLevel.SAFE, ThreatLevel.LOW_RISK]:
                    result.update(
                        {
                            "input_hash": input_hash,
                            "detection_time": datetime.now(),
                            "input_length": len(user_input),
                        }
                    )
                    return DetectionResult(**result)

            flags = []
            total_score = 0

            pattern_score, pattern_flags = self._pattern_matching(normalized)
            total_score += pattern_score
            flags.extend(pattern_flags)

            if self.config.enable_heuristic_analysis:
                heuristic_score, heuristic_flags = self._heuristic_analysis(
                    user_input, normalized
                )
                total_score += heuristic_score
                flags.extend(heuristic_flags)

            if self.config.enable_sequential_analysis:
                seq_score, seq_flags = self._sequential_analysis(user_input)
                total_score += seq_score
                flags.extend(seq_flags)

            if self.config.enable_entropy_analysis:
                entropy_score, entropy_flags = self._entropy_analysis(user_input)
                total_score += entropy_score
                flags.extend(entropy_flags)

            total_score = int(total_score * self.config.sensitivity)

            result = self._calculate_threat(
                total_score, flags, user_input, input_hash, start_time
            )

            self._log_detection(result)

            return result

        except Exception as e:
            self.logger.error(f"Error during analysis: {e}", exc_info=True)
            return self._create_result(
                threat_level=ThreatLevel.SUSPICIOUS,
                flags=[f"analysis_error: {str(e)[:100]}"],
                score=15,
                message="Analysis error - manual review recommended",
                input_length=len(user_input) if "user_input" in locals() else 0,
                input_hash=input_hash if "input_hash" in locals() else "error",
                start_time=start_time,
            )

    def _normalize_input(self, text: str) -> str:
        """Advanced normalization with obfuscation detection"""
        normalized = unicodedata.normalize("NFKC", text)

        normalized = re.sub(
            r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]", "", normalized
        )

        leet_map = {
            "0": "o",
            "1": "i",
            "3": "e",
            "4": "a",
            "5": "s",
            "7": "t",
            "8": "b",
            "@": "a",
            "$": "s",
            "!": "i",
            "|": "i",
            "€": "e",
            "©": "c",
            "®": "r",
            "£": "e",
            "¥": "y",
            "¢": "c",
            "µ": "u",
            "°": "o",
        }
        for leet, normal in leet_map.items():
            normalized = normalized.replace(leet, normal)

        normalized = re.sub(
            r"([a-z])[\.\-_,;:\/\\]+([a-z])", r"\1 \2", normalized, flags=re.IGNORECASE
        )

        normalized = re.sub(r"\s+", " ", normalized)

        normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", normalized)

        return normalized.strip().lower()

    def _is_likely_benign(self, original: str, normalized: str) -> bool:
        """Check if input is likely benign"""
        for pattern in self._benign_patterns:
            if pattern.search(original):
                return True

        benign_indicators = [
            "help me understand",
            "can you explain",
            "how do i",
            "what does",
            "tutorial",
            "example of",
            "learn about",
            "advice on",
            "guide me",
            "teach me",
            "for learning",
            "educational purpose",
        ]

        original_lower = original.lower()
        return any(indicator in original_lower for indicator in benign_indicators)

    def _pattern_matching(self, normalized: str) -> Tuple[int, List[str]]:
        """Pattern matching analysis"""
        score = 0
        flags = []
        patterns = self.pattern_manager.get_patterns()

        for group_name, config in patterns.items():
            group_score = 0
            for pattern, is_strict in config["patterns"]:
                try:
                    matches = list(pattern.finditer(normalized))
                    for match in matches:
                        matched_text = match.group().strip()
                        if len(matched_text) < 4:
                            continue

                        if is_strict and not self._validate_context(normalized, match):
                            continue

                        group_score += 1
                        flags.append(f"{group_name}: '{matched_text[:50]}'")

                except Exception as e:
                    self.logger.debug(f"Pattern matching error: {e}")

            score += group_score * config["weight"]

        return score, flags

    def _validate_context(self, text: str, match: re.Match) -> bool:
        """Validate match context"""
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 100)
        context = text[start:end]

        benign_indicators = [
            r"\b(?:how|why|what|when|where|can|could|should|would|will|help|teach|learn|avoid|prevent|don't|do not|not to)\b",
            r"\?",
            r"\b(?:about|regarding|concerning|regardless)\b",
            r"\b(?:if|whether)\b",
        ]

        for indicator in benign_indicators:
            if re.search(indicator, context):
                attack_indicators = len(
                    re.findall(
                        r"\b(?:system|prompt|instruction|override|bypass|ignore|disregard|reveal)\b",
                        context,
                    )
                )
                return attack_indicators >= 2

        return True

    def _heuristic_analysis(
        self, original: str, normalized: str
    ) -> Tuple[int, List[str]]:
        """Advanced heuristic analysis"""
        score = 0
        flags = []
        n = len(original)

        if n == 0:
            return score, flags

        special_chars = re.findall(r"[<>{}[\]\\|`~!@#$%^&*()+=]", original)
        special_density = len(special_chars) / n
        if special_density > 0.2:
            flags.append("very_high_delimiter_density")
            score += 8
        elif special_density > 0.15:
            flags.append("high_delimiter_density")
            score += 5
        elif special_density > 0.1:
            score += 3

        boundaries = len(
            re.findall(
                r"(?:---|===|```|\*\*\*|\[system\]|<system>|</?prompt>)",
                original,
                re.IGNORECASE,
            )
        )
        if boundaries >= 4:
            flags.append("multiple_context_boundaries")
            score += 10
        elif boundaries >= 3:
            flags.append("context_boundary_markers")
            score += 7
        elif boundaries >= 2:
            score += 4

        risk_words = [
            "ignore",
            "disregard",
            "system",
            "prompt",
            "reveal",
            "instruction",
            "override",
            "bypass",
            "admin",
            "root",
            "jailbreak",
            "DAN",
            "secret",
            "hidden",
            "unrestricted",
        ]
        risk_count = sum(normalized.count(w) for w in risk_words)

        if risk_count >= 5:
            flags.append("very_dense_attack_keywords")
            score += 12
        elif risk_count >= 4:
            flags.append("dense_attack_keywords")
            score += 8
        elif risk_count >= 3:
            score += 5

        if self.config.enable_encoding_detection:
            encoding_patterns = len(
                re.findall(
                    r"(?:\\x[0-9a-f]{2}|\\u[0-9a-f]{4}|&#\d+;|%[0-9a-f]{2})",
                    original,
                    re.IGNORECASE,
                )
            )
            if encoding_patterns >= 5:
                flags.append("multiple_encoding_attempts")
                score += 8
            elif encoding_patterns >= 3:
                flags.append("encoding_detected")
                score += 5

        instruction_words = [
            "ignore",
            "disregard",
            "override",
            "reveal",
            "show",
            "system",
        ]
        repeat_count = sum(
            1 for word in instruction_words if normalized.count(word) >= 2
        )
        if repeat_count >= 3:
            flags.append("repetitive_injection_pattern")
            score += 6
        elif repeat_count >= 2:
            score += 3

        role_pattern = r"you\s+(?:are|become|act\s+as)\s+(?:an?\s+)?(?!.*(?:teacher|tutor|helper|assistant|guide|expert))"
        if re.search(role_pattern, normalized, re.IGNORECASE):
            flags.append("role_manipulation_attempt")
            score += 7

        if n > 5000:
            flags.append("potential_context_stuffing")
            score += 6

        if re.search(r'["\'`]{3,}', original):
            flags.append("quote_manipulation")
            score += 4

        return score, flags

    def _sequential_analysis(self, text: str) -> Tuple[int, List[str]]:
        """Analyze sequence and structure"""
        score = 0
        flags = []

        lines = text.split("\n")
        instruction_lines = []

        for i, line in enumerate(lines):
            line_lower = line.lower()
            if any(
                keyword in line_lower
                for keyword in ["ignore", "disregard", "override", "system:", "prompt:"]
            ):
                instruction_lines.append((i, line_lower))

        if len(instruction_lines) >= 3:
            flags.append("multiple_instruction_lines")
            score += 8

        if len(lines) >= 3:
            middle_index = len(lines) // 2
            middle_line = lines[middle_index].lower()
            if any(
                keyword in middle_line
                for keyword in ["ignore", "disregard", "override"]
            ):
                if (
                    len(lines[middle_index - 1].split()) > 3
                    and len(lines[middle_index + 1].split()) > 3
                ):
                    flags.append("sandwiched_injection_attempt")
                    score += 6

        return score, flags

    def _entropy_analysis(self, text: str) -> Tuple[int, List[str]]:
        """Analyze entropy and randomness"""
        score = 0
        flags = []

        if len(text) < 20:
            return score, flags

        import math
        from collections import Counter

        freq = Counter(text)
        entropy = -sum(
            (count / len(text)) * math.log2(count / len(text))
            for count in freq.values()
        )

        if entropy > 5.5:
            flags.append(f"high_entropy_{entropy:.2f}")
            score += 4

        random_patterns = [
            r"[a-z]{2}\d{2}[a-z]{2}\d{2}",
            r"\b\w{8,12}\b.*\b\w{8,12}\b.*\b\w{8,12}\b",
        ]

        for pattern in random_patterns:
            if re.search(pattern, text):
                flags.append("random_like_pattern")
                score += 3
                break

        return score, flags

    def _analyze_with_reduced_sensitivity(
        self, original: str, normalized: str
    ) -> Dict[str, Any]:
        """Reduced sensitivity analysis for likely benign inputs"""
        flags = []
        score = 0

        severe_patterns = [
            (r"system\s*[:=]\s*(?:ignore|override|bypass|break)", 10),
            (r"\bDAN\s*(?:mode|activate|enabled)", 9),
            (r"<\s*/?\s*system\s*>", 9),
            (r"jailbreak", 8),
            (r"ignore\s+all\s+previous\s+instructions", 12),
        ]

        for pattern_str, weight in severe_patterns:
            if re.search(pattern_str, normalized, re.IGNORECASE):
                flags.append(f"severe_pattern: {pattern_str[:30]}")
                score += weight

        if score >= 16:
            return {
                "threat_level": ThreatLevel.SUSPICIOUS,
                "is_safe": False,
                "flags": flags,
                "confidence": 0.7,
                "threat_score": score,
                "message": "Flagged despite benign context",
                "recommendations": ["Human review recommended"],
            }
        elif score >= 10:
            return {
                "threat_level": ThreatLevel.LOW_RISK,
                "is_safe": True,
                "flags": flags,
                "confidence": 0.4,
                "threat_score": score,
                "message": "Low risk patterns detected",
                "recommendations": ["Monitor for similar patterns"],
            }

        return {
            "threat_level": ThreatLevel.SAFE,
            "is_safe": True,
            "flags": [],
            "confidence": 0.95,
            "threat_score": 0,
            "message": "Input accepted",
            "recommendations": [],
        }

    def _calculate_threat(
        self,
        score: int,
        flags: List[str],
        original: str,
        input_hash: str,
        start_time: datetime,
    ) -> DetectionResult:
        """Calculate final threat level"""
        if self.config.strict_mode:
            thresholds = {
                "critical": 25,
                "dangerous": 18,
                "suspicious": 10,
                "low_risk": 5,
            }
        else:
            thresholds = {
                "critical": 35,
                "dangerous": 25,
                "suspicious": 15,
                "low_risk": 8,
            }

        if score >= thresholds["critical"]:
            threat_level = ThreatLevel.CRITICAL
            message = "CRITICAL: High-confidence injection - BLOCK"
            confidence = min(0.95, 0.8 + (score - thresholds["critical"]) / 50.0)
        elif score >= thresholds["dangerous"]:
            threat_level = ThreatLevel.DANGEROUS
            message = "DANGEROUS: Likely injection attempt - BLOCK"
            confidence = min(0.8, 0.6 + (score - thresholds["dangerous"]) / 40.0)
        elif score >= thresholds["suspicious"]:
            threat_level = ThreatLevel.SUSPICIOUS
            message = "SUSPICIOUS: Potential injection - REVIEW"
            confidence = min(0.6, 0.4 + (score - thresholds["suspicious"]) / 30.0)
        elif score >= thresholds["low_risk"]:
            threat_level = ThreatLevel.LOW_RISK
            message = "LOW RISK: Some concerning patterns"
            confidence = min(0.4, 0.2 + (score - thresholds["low_risk"]) / 20.0)
        else:
            threat_level = ThreatLevel.SAFE
            message = "SAFE: No significant threats detected"
            confidence = max(0.1, 1.0 - score / 10.0)

        recommendations = self._generate_recommendations(threat_level, flags, score)

        metadata = {
            "pattern_version": self.pattern_manager.pattern_version,
            "analysis_time_ms": (datetime.now() - start_time).total_seconds() * 1000,
            "flag_count": len(flags),
        }

        return DetectionResult(
            threat_level=threat_level,
            is_safe=threat_level in [ThreatLevel.SAFE, ThreatLevel.LOW_RISK],
            flags=flags[:10],
            confidence=round(confidence, 2),
            threat_score=score,
            message=message,
            recommendations=recommendations,
            input_length=len(original),
            input_hash=input_hash,
            detection_time=datetime.now(),
            metadata=metadata,
        )

    def _generate_recommendations(
        self, threat_level: ThreatLevel, flags: List[str], score: int
    ) -> List[str]:
        """Generate actionable recommendations"""
        recommendations = []
        flag_str = " ".join(flags).lower()

        if threat_level in [ThreatLevel.CRITICAL, ThreatLevel.DANGEROUS]:
            recommendations.extend(
                [
                    "BLOCK this request immediately",
                    "Log incident for security review",
                    "Consider temporary user suspension",
                    "Increase monitoring for similar patterns",
                ]
            )
        elif threat_level == ThreatLevel.SUSPICIOUS:
            recommendations.extend(
                [
                    "Require human review before processing",
                    "Apply enhanced output filtering",
                    "Monitor user session for escalation",
                    "Consider CAPTCHA or additional verification",
                ]
            )
        elif threat_level == ThreatLevel.LOW_RISK:
            recommendations.extend(
                [
                    "Proceed with caution",
                    "Log for pattern analysis",
                    "Monitor for repeated low-risk patterns",
                ]
            )

        if any(x in flag_str for x in ["encoding", "hex", "base64"]):
            recommendations.append("Decode and re-analyze encoded content")
        if any(x in flag_str for x in ["delimiter", "boundary", "xml", "html"]):
            recommendations.append("Strip and sanitize markup before processing")
        if any(x in flag_str for x in ["jailbreak", "role_manipulation", "dan"]):
            recommendations.append("Reinforce system identity in response")
        if "context_stuffing" in flag_str:
            recommendations.append("Implement input length limits")
        if "repetitive" in flag_str:
            recommendations.append("Check for automated attack patterns")

        return recommendations[:5]

    def _create_safe_result(
        self, input_hash: str, length: int, start_time: datetime
    ) -> DetectionResult:
        """Create safe result"""
        return DetectionResult(
            threat_level=ThreatLevel.SAFE,
            is_safe=True,
            flags=[],
            confidence=1.0,
            threat_score=0,
            message="Input accepted",
            recommendations=[],
            input_length=length,
            input_hash=input_hash,
            detection_time=datetime.now(),
            metadata={
                "analysis_time_ms": (datetime.now() - start_time).total_seconds() * 1000
            },
        )

    def _create_result(
        self,
        threat_level: ThreatLevel,
        flags: List[str],
        score: int,
        message: str,
        input_length: int,
        input_hash: str,
        start_time: datetime,
    ) -> DetectionResult:
        """Create result with given parameters"""
        recommendations = self._generate_recommendations(threat_level, flags, score)

        return DetectionResult(
            threat_level=threat_level,
            is_safe=threat_level in [ThreatLevel.SAFE, ThreatLevel.LOW_RISK],
            flags=flags,
            confidence=0.7
            if threat_level in [ThreatLevel.DANGEROUS, ThreatLevel.CRITICAL]
            else 0.5,
            threat_score=score,
            message=message,
            recommendations=recommendations,
            input_length=input_length,
            input_hash=input_hash,
            detection_time=datetime.now(),
            metadata={
                "analysis_time_ms": (datetime.now() - start_time).total_seconds() * 1000
            },
        )

    def _log_detection(self, result: DetectionResult):
        """Log detection result"""
        if result.threat_level in [ThreatLevel.CRITICAL, ThreatLevel.DANGEROUS]:
            self.logger.warning(
                f"THREAT DETECTED: {result.threat_level.value.upper()} "
                f"(score: {result.threat_score}, confidence: {result.confidence})"
            )
        elif result.threat_level == ThreatLevel.SUSPICIOUS:
            self.logger.info(
                f"Suspicious input detected: {result.threat_level.value} "
                f"(score: {result.threat_score})"
            )
        else:
            self.logger.debug(
                f"Input analyzed: {result.threat_level.value} "
                f"(score: {result.threat_score})"
            )


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

    def __init__(self, config: Optional[DetectionConfig] = None):
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

    def check_batch(self, inputs: List[str]) -> List[DetectionResult]:
        """Analyze multiple inputs"""
        return [self.check(input_text) for input_text in inputs]

    def get_stats(self) -> Dict[str, Any]:
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


def quick_check(user_input: str, strict: bool = False) -> Dict[str, Any]:
    """Quick one-off check for prompt injection"""
    guard = create_guard(strict=strict)
    result = guard.check(user_input)
    return result.to_dict()
