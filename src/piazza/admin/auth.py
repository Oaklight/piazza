"""Authentication module for admin panel.

Provides simple token-based authentication using constant-time
comparison to prevent timing attacks.
"""

import hashlib
import json
import secrets
from http.server import BaseHTTPRequestHandler


class TokenAuth:
    """Simple token-based authentication.

    Args:
        token: Optional authentication token. If None, a random
            32-character hex token is generated.

    Example:
        >>> auth = TokenAuth()
        >>> print(f"Use token: {auth.token}")
        >>> auth.verify("some_token")  # Returns True/False
    """

    def __init__(self, token: str | None = None) -> None:
        if token is None:
            self._token = secrets.token_hex(16)
        else:
            self._token = token

    @property
    def token(self) -> str:
        """Get the authentication token."""
        return self._token

    def verify(self, provided_token: str) -> bool:
        """Verify a provided token using constant-time comparison.

        Args:
            provided_token: The token to verify.

        Returns:
            True if the token matches, False otherwise.
        """
        expected_hash = hashlib.sha256(self._token.encode()).digest()
        provided_hash = hashlib.sha256(provided_token.encode()).digest()
        return secrets.compare_digest(expected_hash, provided_hash)

    def require_auth(self, handler: BaseHTTPRequestHandler) -> bool:
        """Check Authorization header and send 401 if invalid.

        Expects "Bearer <token>" format.

        Args:
            handler: The HTTP request handler to check.

        Returns:
            True if valid, False otherwise (401 already sent).
        """
        auth_header = handler.headers.get("Authorization", "")

        if not auth_header:
            self._send_unauthorized(handler, "Missing Authorization header")
            return False

        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            self._send_unauthorized(handler, "Invalid Authorization format")
            return False

        if not self.verify(parts[1]):
            self._send_unauthorized(handler, "Invalid token")
            return False

        return True

    def _send_unauthorized(self, handler: BaseHTTPRequestHandler, message: str) -> None:
        """Send a 401 Unauthorized response."""
        handler.send_response(401)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("WWW-Authenticate", 'Bearer realm="admin"')
        handler.end_headers()
        response = json.dumps({"error": "Unauthorized", "message": message})
        handler.wfile.write(response.encode("utf-8"))
