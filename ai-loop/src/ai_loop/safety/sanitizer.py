"""Input sanitization for Linear issue content."""

from __future__ import annotations

import re

# Maximum content length (10KB)
MAX_CONTENT_LENGTH = 10_000

# Patterns that could be injection attempts
INJECTION_PATTERNS = [
    # Shell command injection
    (r"\$\([^)]+\)", "[FILTERED:subshell]"),
    # Single backtick command substitution (not triple backticks for code blocks)
    (r"(?<!`)`(?!``)([^`\n]+)`(?!`)", "[FILTERED:backtick]"),
    (r";\s*\w+", "[FILTERED:command-chain]"),
    (r"\|\s*\w+", "[FILTERED:pipe]"),
    (r"&&\s*\w+", "[FILTERED:and-chain]"),
    (r"\|\|\s*\w+", "[FILTERED:or-chain]"),
    # Path traversal
    (r"\.\.\/", "[FILTERED:path-traversal]"),
    (r"\.\.\\\\", "[FILTERED:path-traversal]"),
    # XML/HTML injection (could affect prompt parsing)
    (r"<script[^>]*>.*?</script>", "[FILTERED:script]"),
    (r"<iframe[^>]*>.*?</iframe>", "[FILTERED:iframe]"),
    # Environment variable expansion
    (r"\$\{[^}]+\}", "[FILTERED:env-expansion]"),
    (r"\$[A-Z_][A-Z0-9_]*", "[FILTERED:env-var]"),
    # ANSI escape sequences
    (r"\x1b\[[0-9;]*[a-zA-Z]", "[FILTERED:ansi]"),
    # Null bytes
    (r"\x00", "[FILTERED:null]"),
]


def sanitize_issue_content(content: str | None) -> str:
    """
    Sanitize Linear issue content for safe use in prompts.

    - Truncates to MAX_CONTENT_LENGTH
    - Filters common injection patterns
    - Ensures content is treated as DATA only
    """
    if not content:
        return ""

    # Truncate
    if len(content) > MAX_CONTENT_LENGTH:
        content = content[:MAX_CONTENT_LENGTH] + "\n\n[TRUNCATED]"

    # Apply injection filters
    for pattern, replacement in INJECTION_PATTERNS:
        content = re.sub(pattern, replacement, content, flags=re.IGNORECASE | re.DOTALL)

    return content


def sanitize_issue_title(title: str) -> str:
    """Sanitize issue title (shorter, stricter)."""
    # Limit title length
    max_title_length = 200
    if len(title) > max_title_length:
        title = title[:max_title_length] + "..."

    # Remove newlines
    title = title.replace("\n", " ").replace("\r", " ")

    # Apply same injection filters
    for pattern, replacement in INJECTION_PATTERNS:
        title = re.sub(pattern, replacement, title, flags=re.IGNORECASE | re.DOTALL)

    return title


def escape_for_shell(value: str) -> str:
    """Escape a value for safe shell interpolation."""
    # Single quote the value and escape any single quotes within
    return "'" + value.replace("'", "'\"'\"'") + "'"


def is_safe_path(path: str, allowed_root: str) -> bool:
    """Check if a path is within the allowed root directory."""
    from pathlib import Path

    try:
        resolved = Path(path).resolve()
        allowed = Path(allowed_root).resolve()
        return str(resolved).startswith(str(allowed))
    except (ValueError, OSError):
        return False
