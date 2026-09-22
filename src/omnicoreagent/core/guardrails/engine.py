from __future__ import annotations

import hashlib
import logging
import re
import sys
import unicodedata
from datetime import datetime

from omnicoreagent.core.guardrails.models import DetectionConfig, DetectionResult, ThreatLevel
from omnicoreagent.core.guardrails.patterns import PatternManager


class DetectionEngine:
    """Core detection engine with multiple analysis stages"""

    def __init__(self, config: DetectionConfig):
        self.config = config
        self.pattern_manager = PatternManager()
        self.logger = self._setup_logger()

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

            if self.config.allowlist_patterns and any(
                re.search(p, user_input, re.IGNORECASE)
                for p in self.config.allowlist_patterns
            ):
                # A trusted pattern may bypass ordinary false positives, but
                # it cannot authorize a known instruction override, extraction,
                # jailbreak, or context-manipulation pattern.
                _, allowlist_flags = self._pattern_matching(normalized, user_input)
                if not self._has_high_risk_pattern(allowlist_flags):
                    return self._create_safe_result(
                        input_hash, len(user_input), start_time
                    )

            # Evidence is intent addressed to the model, or content hidden
            # from a reader. Structure, vocabulary, length and entropy are
            # not evidence: a diff, a docstring, a markdown rule, an
            # identifier, the words "system" and "override" all belong to
            # ordinary developer text, and a screen that scores them cannot
            # be trusted. The verdict comes from the kinds of evidence found,
            # never from adding up weak signals.
            pattern_score, flags = self._pattern_matching(normalized, user_input)
            if self.config.enable_encoding_detection:
                flags.extend(self._encoding_flags(user_input))
            total_score = int(pattern_score * self.config.sensitivity)

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

    # Evidence that the text tries to redirect the model, or to reach its
    # instructions: one of these is enough to block.
    STRONG_KINDS = frozenset(
        {
            "instruction_override",
            "prompt_extraction",
            "jailbreak_roleplay",
            "context_manipulation",
            "payload_decode_intent",
            "custom",
        }
    )
    # Evidence that something is being hidden or framed: recorded, and
    # blocking only beside strong evidence (or in strict mode).
    WEAK_KINDS = frozenset({"delimiter_injection", "obfuscation_techniques", "payload_encoding"})

    @classmethod
    def _kinds(cls, flags: list[str]) -> tuple[set[str], set[str]]:
        strong, weak = set(), set()
        for flag in flags:
            group = flag.split(":", 1)[0]
            if group in cls.STRONG_KINDS or group.startswith("custom"):
                strong.add(group)
            elif group in cls.WEAK_KINDS:
                weak.add(group)
        return strong, weak

    @classmethod
    def _has_high_risk_pattern(cls, flags: list[str]) -> bool:
        strong, _ = cls._kinds(flags)
        return bool(strong)

    @staticmethod
    def _encoding_flags(original: str) -> list[str]:
        """Escape sequences that hide text from a reader (three or more)."""
        escapes = len(
            re.findall(r"(?:\\x[0-9a-f]{2}|\\u[0-9a-f]{4}|&#\d+;|%[0-9a-f]{2})", original, re.IGNORECASE)
        )
        return [f"payload_encoding: {escapes} escape sequences"] if escapes >= 3 else []

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
        def normalize_leet_token(match: re.Match[str]) -> str:
            token = match.group(0)
            if not _could_be_leet(token):
                return token
            for leet, normal in leet_map.items():
                token = token.replace(leet, normal)
            return token

        normalized = re.sub(r"[\w@$!|€©®£¥¢µ°]+", normalize_leet_token, normalized)

        # Letters spaced out to slip past a word match ("o v e r r i d e")
        # are joined, so the intent behind them is matched as written.
        normalized = re.sub(
            r"\b((?:[A-Za-z] ){3,}[A-Za-z])\b",
            lambda match: match.group(1).replace(" ", ""),
            normalized,
        )

        normalized = re.sub(
            r"([a-z])[\.\-_,;:\/\\]+([a-z])", r"\1 \2", normalized, flags=re.IGNORECASE
        )

        normalized = re.sub(r"\s+", " ", normalized)

        normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", normalized)

        return normalized.strip().lower()

    def _pattern_matching(
        self, normalized: str, original: str | None = None
    ) -> tuple[int, list[str]]:
        """Pattern matching analysis"""
        score = 0
        flags = []
        patterns = self.pattern_manager.get_patterns()

        for group_name, config in patterns.items():
            group_score = 0
            # A group may ask for the text as written, when folding it
            # (leetspeak, separators) would create what the group looks for.
            text = normalized
            if config.get("match") == "original" and original is not None:
                text = original.lower()
            for pattern, is_strict in config["patterns"]:
                try:
                    matches = list(pattern.finditer(text))
                    for match in matches:
                        matched_text = match.group().strip()
                        if len(matched_text) < 4:
                            continue

                        if is_strict and not self._validate_context(text, match):
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

    def _calculate_threat(
        self,
        score: int,
        flags: list[str],
        original: str,
        input_hash: str,
        start_time: datetime,
    ) -> DetectionResult:
        """The verdict, from the kinds of evidence found.

        Two kinds of strong evidence, or strong evidence beside hidden
        content, is critical; one kind of strong evidence is dangerous;
        hidden content or framing alone is suspicious (dangerous in strict
        mode); nothing found is safe. The score is kept for callers that
        read it, but it does not decide.
        """
        strong, weak = self._kinds(flags)
        if len(strong) >= 2 or (strong and weak):
            threat_level = ThreatLevel.CRITICAL
            message = "CRITICAL: High-confidence injection - BLOCK"
            confidence = 0.95
        elif strong:
            threat_level = ThreatLevel.DANGEROUS
            message = "DANGEROUS: Likely injection attempt - BLOCK"
            confidence = 0.85
        elif weak and self.config.strict_mode:
            threat_level = ThreatLevel.DANGEROUS
            message = "DANGEROUS: Hidden or framed content (strict mode) - BLOCK"
            confidence = 0.6
        elif weak:
            threat_level = ThreatLevel.SUSPICIOUS
            message = "SUSPICIOUS: Hidden or framed content - REVIEW"
            confidence = 0.5
        else:
            threat_level = ThreatLevel.SAFE
            message = "SAFE: No significant threats detected"
            confidence = 1.0

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
        self, threat_level: ThreatLevel, flags: list[str], score: int
    ) -> list[str]:
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
        flags: list[str],
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



def _word_count(text: str, word: str) -> int:
    return len(re.findall(rf"\b{re.escape(word)}\b", text))


_HEX = re.compile(r"[0-9a-f]+", re.IGNORECASE)


def _could_be_leet(token: str) -> bool:
    """Whether folding digits to letters could reveal a word in ``token``.

    A hexadecimal identifier (a run id, a commit SHA, a trace id) has digits
    among letters but is never a word: folding it invents letter runs and
    "words" that were never written. An identifier with underscores is code.
    """
    has_alpha = any(char.isalpha() for char in token)
    has_digit = any(char.isdigit() for char in token)
    if not (has_alpha and has_digit):
        return False
    if "_" in token:
        return False
    return _HEX.fullmatch(token) is None
