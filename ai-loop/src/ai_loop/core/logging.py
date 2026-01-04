"""Single logging path for AI Loop. Replaces all ad-hoc print() calls."""

import sys
from datetime import datetime

# High-signal prefixes shown in terminal (all emitted prefixes)
HIGH_SIGNAL = {
    "API",
    "BATCH",
    "PIPELINE",
    "PLANNING",
    "PLAN_GATE",
    "REFINING",
    "IMPLEMENTING",
    "CODE_GATE",
    "CLAUDE",
    "GATE",
    "ERROR",
}


def log(prefix: str, message: str) -> None:
    """Log with timestamp. Always to stderr (won't interfere with stdout capture)."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{prefix}] {message}", file=sys.stderr, flush=True)


def is_high_signal(line: str) -> bool:
    """Check if a log line should be shown in terminal."""
    # Error indicators always shown
    if any(err in line for err in ("ERROR", "Traceback", "Exception", "FAILED")):
        return True
    # Check for high-signal prefixes
    for prefix in HIGH_SIGNAL:
        if f"[{prefix}]" in line:
            return True
    return False
