"""HTTP request handler for admin panel.

Thin dispatcher that routes requests to focused handler modules
under ``routes/``.  Uses dict-based dispatch for easy extensibility.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any, ClassVar

from .routes import _shared, channels, dashboard, messages, subscriptions, tokens, ui

if TYPE_CHECKING:
    from piazza.bus import Bus
    from piazza.token_store import TokenStore

    from .auth import SessionAuth


# GET routes: path → handler(self)
_GET_ROUTES: dict[str, Callable[..., None]] = {
    "/": ui.handle_root,
    "/api/stats": dashboard.handle_get_stats,
    "/api/stats/throughput": dashboard.handle_get_throughput,
    "/api/channels": channels.handle_get_channels,
    "/api/messages": lambda self, query: messages.handle_get_messages(self, query),
    "/api/subscriptions": subscriptions.handle_get_subscriptions,
    "/api/tokens": tokens.handle_list_tokens,
    "/api/auth-check": lambda self: (
        self.auth.handle_auth_check(self)
        if self.auth
        else _shared.send_json_response(self, {"authenticated": True, "required": False})
    ),
}

# POST routes: path → handler(self, body)
_POST_ROUTES: dict[str, Callable[..., None]] = {
    "/api/messages": lambda self, body: messages.handle_publish_message(self, body),
    "/api/tokens": lambda self, body: tokens.handle_create_token(self, body),
    "/api/login": lambda self, body: (
        self.auth.handle_login(self, body)
        if self.auth
        else _shared.send_json_response(self, {"ok": True})
    ),
    "/api/logout": lambda self, body: (
        self.auth.handle_logout(self)
        if self.auth
        else _shared.send_json_response(self, {"ok": True})
    ),
}


class AdminRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for piazza admin panel.

    Routes incoming requests to focused handler modules for:
    - Dashboard statistics and throughput
    - Channel listing and details
    - Message browsing and publishing
    - Subscription visibility
    - Token management (create, delete, rotate)

    Class Attributes:
        bus: The Bus instance to monitor.
        auth: Optional SessionAuth instance for authentication.
        serve_ui: Whether to serve the admin UI at root path.
        token_store: Optional TokenStore for agent token management.
    """

    bus: ClassVar[Bus]
    auth: ClassVar[SessionAuth | None]
    serve_ui: ClassVar[bool]
    token_store: ClassVar[TokenStore | None]

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP logging."""

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.auth and not self.auth.require_auth(self):
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # Exact match
        handler_fn = _GET_ROUTES.get(path)
        if handler_fn is not None:
            if path == "/api/messages":
                handler_fn(self, query)
            else:
                handler_fn(self)
            return

        # Prefix match: /api/channels/{name}
        if path.startswith("/api/channels/"):
            name = urllib.parse.unquote(path[len("/api/channels/") :])
            channels.handle_get_channel(self, name)
            return

        _shared.send_not_found(self)

    def do_POST(self) -> None:
        """Handle POST requests."""
        if self.auth and not self.auth.require_auth(self):
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # Exact match
        handler_fn = _POST_ROUTES.get(path)
        if handler_fn is not None:
            handler_fn(self, body)
            return

        # Prefix match: /api/tokens/{id}/rotate
        if path.startswith("/api/tokens/") and path.endswith("/rotate"):
            token_id = path[len("/api/tokens/") : -len("/rotate")]
            tokens.handle_rotate_token(self, token_id)
            return

        _shared.send_not_found(self)

    def do_DELETE(self) -> None:
        """Handle DELETE requests."""
        if self.auth and not self.auth.require_auth(self):
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # DELETE /api/tokens/{id}
        if path.startswith("/api/tokens/"):
            token_id = urllib.parse.unquote(path[len("/api/tokens/") :])
            tokens.handle_delete_token(self, token_id)
            return

        _shared.send_not_found(self)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(204)
        _shared.send_cors_headers(self)
        self.end_headers()
