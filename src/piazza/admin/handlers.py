"""HTTP request handler for admin panel.

Thin dispatcher that routes requests to focused handler modules
under ``routes/``.  Uses dict-based dispatch for easy extensibility.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any, ClassVar

from .routes import _shared, channels, dashboard, messages, subscriptions, ui

if TYPE_CHECKING:
    from piazza.bus import Bus

    from .auth import TokenAuth


# GET routes: path → handler(self)
_GET_ROUTES: dict[str, Callable[..., None]] = {
    "/": ui.handle_root,
    "/api/stats": dashboard.handle_get_stats,
    "/api/stats/throughput": dashboard.handle_get_throughput,
    "/api/channels": channels.handle_get_channels,
    "/api/messages": lambda self, query: messages.handle_get_messages(self, query),
    "/api/subscriptions": subscriptions.handle_get_subscriptions,
}

# POST routes: path → handler(self, body)
_POST_ROUTES: dict[str, Callable[..., None]] = {
    "/api/messages": lambda self, body: messages.handle_publish_message(self, body),
}


class AdminRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for piazza admin panel.

    Routes incoming requests to focused handler modules for:
    - Dashboard statistics and throughput
    - Channel listing and details
    - Message browsing and publishing
    - Subscription visibility

    Class Attributes:
        bus: The Bus instance to monitor.
        auth: Optional TokenAuth instance for authentication.
        serve_ui: Whether to serve the admin UI at root path.
    """

    bus: ClassVar[Bus]
    auth: ClassVar[TokenAuth | None]
    serve_ui: ClassVar[bool]

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

        handler_fn = _POST_ROUTES.get(path)
        if handler_fn is not None:
            handler_fn(self, body)
            return

        _shared.send_not_found(self)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(204)
        _shared.send_cors_headers(self)
        self.end_headers()
