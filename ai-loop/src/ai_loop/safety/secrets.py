"""Secrets scanning and redaction for LLM outputs."""

from __future__ import annotations

import re
from typing import NamedTuple


class SecretMatch(NamedTuple):
    """A detected secret match."""

    pattern_name: str
    start: int
    end: int
    redacted: str


# Patterns for common secrets
SECRET_PATTERNS = [
    # API Keys (generic)
    ("api_key_generic", r"(?i)(api[_-]?key|apikey)['\"]?\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{20,})['\"]?"),
    # AWS
    ("aws_access_key", r"AKIA[0-9A-Z]{16}"),
    ("aws_secret_key", r"(?i)aws[_-]?secret[_-]?access[_-]?key['\"]?\s*[:=]\s*['\"]?([a-zA-Z0-9/+=]{40})['\"]?"),
    # GitHub
    ("github_token", r"gh[pousr]_[A-Za-z0-9_]{36,}"),
    ("github_pat", r"github_pat_[A-Za-z0-9_]{22,}"),
    # Linear
    ("linear_api_key", r"lin_api_[A-Za-z0-9]{32,}"),
    # OpenAI
    ("openai_key", r"sk-[A-Za-z0-9]{20,}T3BlbkFJ[A-Za-z0-9]{20,}"),
    ("openai_key_proj", r"sk-proj-[A-Za-z0-9\-_]{40,}"),
    # Anthropic
    ("anthropic_key", r"sk-ant-[A-Za-z0-9\-_]{40,}"),
    # JWT
    ("jwt", r"eyJ[A-Za-z0-9_-]*\.eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*"),
    # Private Keys
    ("private_key", r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    # Generic secrets in env format
    ("env_secret", r"(?i)(password|secret|token|credential|auth)['\"]?\s*[:=]\s*['\"]?([^\s'\"]{8,})['\"]?"),
    # Base64 encoded secrets (long strings)
    ("base64_secret", r"(?i)(secret|password|key|token)_?base64['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9+/=]{40,})['\"]?"),
]


def scan_for_secrets(text: str) -> list[SecretMatch]:
    """
    Scan text for potential secrets.

    Returns list of SecretMatch objects with positions.
    """
    matches: list[SecretMatch] = []

    for pattern_name, pattern in SECRET_PATTERNS:
        for match in re.finditer(pattern, text):
            matches.append(
                SecretMatch(
                    pattern_name=pattern_name,
                    start=match.start(),
                    end=match.end(),
                    redacted=f"[REDACTED:{pattern_name}]",
                )
            )

    # Sort by position for consistent replacement
    matches.sort(key=lambda m: m.start)
    return matches


def redact_secrets(text: str) -> tuple[str, list[SecretMatch]]:
    """
    Redact all detected secrets from text.

    Returns (redacted_text, list of matches).
    """
    matches = scan_for_secrets(text)

    if not matches:
        return text, []

    # Replace from end to preserve positions
    result = text
    for match in reversed(matches):
        result = result[: match.start] + match.redacted + result[match.end :]

    return result, matches


def is_likely_secret(value: str) -> bool:
    """
    Heuristic check if a value looks like it could be a secret.

    Used for extra caution with unknown values.
    """
    # Check against all patterns
    for _, pattern in SECRET_PATTERNS:
        if re.search(pattern, value):
            return True

    # Additional heuristics
    # High entropy string (lots of different characters)
    if len(value) > 20:
        unique_chars = len(set(value))
        if unique_chars / len(value) > 0.7:
            # Check it's not just a sentence
            if not re.search(r"\s", value):
                return True

    return False


def safe_log_value(key: str, value: str, max_length: int = 50) -> str:
    """
    Return a safe-to-log representation of a value.

    Redacts if key suggests sensitivity or value looks like a secret.
    """
    sensitive_keys = {
        "password",
        "secret",
        "token",
        "key",
        "credential",
        "auth",
        "api",
        "bearer",
        "authorization",
    }

    key_lower = key.lower()
    for sensitive in sensitive_keys:
        if sensitive in key_lower:
            return "[REDACTED]"

    if is_likely_secret(value):
        return "[REDACTED]"

    if len(value) > max_length:
        return value[:max_length] + "..."

    return value
