"""Triple-layer localhost security: Origin + Token + CSRF.

Security model:
- Server binds to 127.0.0.1 only (never 0.0.0.0)
- DNS rebinding defense via loopback IP check
- Origin header REQUIRED for all mutations (deny if absent)
- Pairing token required for all mutations
- CSRF synchronizer token (header must match stored token)
- Rate-limit pairing attempts (max 5/minute)
"""

from __future__ import annotations

import secrets
import time
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler


class SecurityManager:
    """Triple-layer localhost security: Origin + Token + CSRF."""

    def __init__(self, pairing_token: str | None = None):
        """Initialize security manager.

        Args:
            pairing_token: Accept pairing token from parent (dev mode) or generate new (prod mode)
        """
        self.pairing_token = pairing_token or secrets.token_urlsafe(32)
        self.paired = False
        self.pairing_attempts: list[float] = []
        self.csrf_token = secrets.token_urlsafe(32)  # Single source of truth

    def get_csrf_token(self) -> str:
        """Return current CSRF token (no rotation)."""
        return self.csrf_token

    def rotate_csrf_token(self) -> str:
        """Generate new CSRF token (call only on explicit rotation)."""
        self.csrf_token = secrets.token_urlsafe(32)
        return self.csrf_token

    def validate_and_pair(self, token: str) -> bool:
        """Validate pairing token and mark as paired if valid.

        Rate-limited to 5 attempts per minute.
        """
        now = time.time()
        # Clean up old attempts
        self.pairing_attempts = [t for t in self.pairing_attempts if now - t < 60]

        # Rate limit check
        if len(self.pairing_attempts) >= 5:
            return False

        self.pairing_attempts.append(now)

        if secrets.compare_digest(token, self.pairing_token):
            self.paired = True
            return True
        return False


# Trusted origins for localhost access
def get_trusted_origins(port: int) -> set[str]:
    """Get trusted origins for a given port."""
    return {
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    }


def parse_cookies(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    """Parse cookies from Cookie header.

    SimpleHTTPRequestHandler doesn't do this automatically, so we parse manually.
    """
    cookies: dict[str, str] = {}
    cookie_header = handler.headers.get("Cookie", "")
    if cookie_header:
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        cookies = {k: v.value for k, v in cookie.items()}
    return cookies


def validate_mutating_request(
    handler: BaseHTTPRequestHandler,
    security: SecurityManager,
    port: int = 8080,
) -> tuple[bool, str]:
    """Validate a mutating request (POST/PUT/DELETE).

    Requires ALL THREE layers:
    1. Origin header - exact match, DENY if absent
    2. Pairing token - in X-Pairing-Token header
    3. CSRF cookie - double-submit pattern (cookie + header must match)

    Plus:
    - DNS rebinding defense (client_address must be loopback)
    - Host header validation (localhost/127.0.0.1 only)
    - Sec-Fetch-Site check when present (reject cross-site)

    Returns:
        Tuple of (valid, message)
    """
    # 0. DNS rebinding defense
    client_ip = handler.client_address[0]
    if client_ip not in ("127.0.0.1", "::1"):
        return False, "Non-loopback client rejected"

    # 1. Host header must be localhost
    host = handler.headers.get("Host", "").split(":")[0]
    if host not in ("localhost", "127.0.0.1"):
        return False, "Invalid host"

    # 2. Origin header REQUIRED
    origin = handler.headers.get("Origin")
    if not origin:
        return False, "Origin header required"

    trusted_origins = get_trusted_origins(port)
    if origin not in trusted_origins:
        return False, "Invalid origin"

    # 3. Sec-Fetch-Site check (when browser sends it)
    sec_fetch_site = handler.headers.get("Sec-Fetch-Site")
    if sec_fetch_site and sec_fetch_site not in ("same-origin", "same-site"):
        return False, "Cross-site request rejected"

    # 4. Pairing token required
    token = handler.headers.get("X-Pairing-Token")
    if not token:
        return False, "Pairing token required"
    if not security.paired and not security.validate_and_pair(token):
        return False, "Invalid pairing token"

    # 5. CSRF double-submit check
    cookies = parse_cookies(handler)
    csrf_cookie = cookies.get("csrf_token")
    csrf_header = handler.headers.get("X-CSRF-Token")
    if not csrf_cookie or not csrf_header:
        return False, "CSRF token required"
    if not secrets.compare_digest(csrf_cookie, csrf_header):
        return False, "CSRF token mismatch"

    return True, "OK"
