"""Root path handler — serve admin UI or API index."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._shared import send_cors_headers, send_json_response

if TYPE_CHECKING:
    from ..handlers import AdminRequestHandler


def handle_root(handler: AdminRequestHandler) -> None:
    """Handle root path — serve UI HTML or API endpoint listing."""
    if handler.serve_ui:
        from ..static import ADMIN_HTML

        handler.send_response(200)
        handler.send_header("Content-Type", "text/html")
        send_cors_headers(handler)
        handler.end_headers()
        handler.wfile.write(ADMIN_HTML.encode("utf-8"))
    else:
        send_json_response(
            handler,
            {
                "name": "Piazza Admin API",
                "version": "1.0.0",
                "endpoints": [
                    "GET /api/stats",
                    "GET /api/stats/throughput",
                    "GET /api/channels",
                    "GET /api/channels/{name}",
                    "GET /api/messages?channel=...",
                    "POST /api/messages",
                    "GET /api/subscriptions",
                ],
            },
        )
