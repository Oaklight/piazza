"""Authentication module for admin panel.

Provides session-cookie-based authentication so the ``Authorization``
header is free for agent Bearer tokens. Static assets (HTML, JS, CSS)
are served without auth so the login overlay can render.

Session tokens are HMAC-SHA256 of the admin password + a random nonce,
stored in an ``HttpOnly; SameSite=Strict`` cookie.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from http import cookies
from http.server import BaseHTTPRequestHandler


class SessionAuth:
    """Session-cookie-based admin authentication.

    Args:
        password: Admin password. If None, a random 16-char hex
            password is generated.

    Example:
        >>> auth = SessionAuth("my-secret")
        >>> token = auth.create_session()
        >>> auth.validate_session(token)  # True
    """

    COOKIE_NAME = "piazza_session"

    def __init__(self, password: str | None = None) -> None:
        if password is None:
            self._password = secrets.token_hex(16)
        else:
            self._password = password
        self._nonce = secrets.token_hex(16)

    @property
    def password(self) -> str:
        """Get the admin password."""
        return self._password

    def create_session(self) -> str:
        """Create a session token derived from password + nonce.

        Returns:
            HMAC-SHA256 hex string to store in cookie.
        """
        return hmac.new(
            self._nonce.encode(),
            self._password.encode(),
            hashlib.sha256,
        ).hexdigest()

    def validate_session(self, session_token: str) -> bool:
        """Validate a session token using constant-time comparison.

        Args:
            session_token: Token from the session cookie.

        Returns:
            True if valid.
        """
        expected = self.create_session()
        return hmac.compare_digest(session_token, expected)

    def check_password(self, provided: str) -> bool:
        """Check a password using constant-time comparison.

        Args:
            provided: The password to check.

        Returns:
            True if correct.
        """
        expected_hash = hashlib.sha256(self._password.encode()).digest()
        provided_hash = hashlib.sha256(provided.encode()).digest()
        return secrets.compare_digest(expected_hash, provided_hash)

    def require_auth(self, handler: BaseHTTPRequestHandler) -> bool:
        """Check session cookie and gate API access.

        - ``/api/login``, ``/api/logout``, ``/api/auth-check``: always allowed
        - ``/api/*``: require valid session cookie → 401 if missing
        - Everything else (``/``, static assets): allowed without auth

        Args:
            handler: The HTTP request handler to check.

        Returns:
            True if request should proceed, False if 401 was sent.
        """
        path = handler.path.split("?")[0]

        # Login, logout, and auth-check are always accessible
        if path in ("/api/login", "/api/logout", "/api/auth-check"):
            return True

        # Non-API paths (HTML, static assets) pass through
        if not path.startswith("/api/"):
            return True

        # API paths require valid session cookie
        session_token = self._extract_cookie(handler)
        if session_token and self.validate_session(session_token):
            return True

        self._send_unauthorized(handler, "Admin authentication required")
        return False

    def handle_login(self, handler: BaseHTTPRequestHandler, body: bytes) -> None:
        """Handle ``POST /api/login`` — validate password, set cookie.

        Args:
            handler: The HTTP request handler.
            body: Raw request body (JSON with ``password`` field).
        """
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json(handler, {"error": "Invalid JSON"}, 400)
            return

        password = data.get("password", "")
        if not self.check_password(password):
            self._send_json(handler, {"error": "Invalid password"}, 401)
            return

        session_token = self.create_session()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header(
            "Set-Cookie",
            f"{self.COOKIE_NAME}={session_token}; HttpOnly; SameSite=Strict; Path=/",
        )
        _send_cors(handler)
        handler.end_headers()
        handler.wfile.write(json.dumps({"ok": True}).encode())

    def handle_auth_check(self, handler: BaseHTTPRequestHandler) -> None:
        """Handle ``GET /api/auth-check`` — return auth status.

        Args:
            handler: The HTTP request handler.
        """
        session_token = self._extract_cookie(handler)
        authenticated = bool(session_token and self.validate_session(session_token))
        self._send_json(handler, {"authenticated": authenticated, "required": True})

    def handle_logout(self, handler: BaseHTTPRequestHandler) -> None:
        """Handle ``POST /api/logout`` — clear session cookie.

        Args:
            handler: The HTTP request handler.
        """
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header(
            "Set-Cookie",
            f"{self.COOKIE_NAME}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0",
        )
        _send_cors(handler)
        handler.end_headers()
        handler.wfile.write(json.dumps({"ok": True}).encode())

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _extract_cookie(handler: BaseHTTPRequestHandler) -> str | None:
        """Extract session cookie value from request headers."""
        cookie_header = handler.headers.get("Cookie", "")
        if not cookie_header:
            return None
        try:
            c = cookies.SimpleCookie(cookie_header)
            morsel = c.get(SessionAuth.COOKIE_NAME)
            return morsel.value if morsel else None
        except cookies.CookieError:
            return None

    @staticmethod
    def _send_json(handler: BaseHTTPRequestHandler, data: dict, status: int = 200) -> None:
        """Send a JSON response."""
        body = json.dumps(data).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        _send_cors(handler)
        handler.end_headers()
        handler.wfile.write(body)

    @staticmethod
    def _send_unauthorized(handler: BaseHTTPRequestHandler, message: str) -> None:
        """Send a 401 Unauthorized response."""
        body = json.dumps({"error": "Unauthorized", "message": message}).encode()
        handler.send_response(401)
        handler.send_header("Content-Type", "application/json")
        _send_cors(handler)
        handler.end_headers()
        handler.wfile.write(body)


# Keep backward-compatible name for imports (deprecated)
TokenAuth = SessionAuth


def _send_cors(handler: BaseHTTPRequestHandler) -> None:
    """Send CORS headers for credentialed requests."""
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Credentials", "true")
