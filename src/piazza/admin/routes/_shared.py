"""Shared response helpers for admin route handlers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..handlers import AdminRequestHandler


def send_cors_headers(handler: AdminRequestHandler) -> None:
    """Send CORS headers on the response."""
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")


def json_serializer(obj: Any) -> Any:
    """Custom JSON serializer for datetime objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def send_json_response(handler: AdminRequestHandler, data: Any, status: int = 200) -> None:
    """Send a JSON response with CORS headers."""
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    send_cors_headers(handler)
    handler.end_headers()
    response = json.dumps(data, default=json_serializer)
    handler.wfile.write(response.encode("utf-8"))


def send_error_response(
    handler: AdminRequestHandler, status: int, error: str, message: str
) -> None:
    """Send an error JSON response."""
    send_json_response(handler, {"error": error, "message": message}, status)


def send_not_found(handler: AdminRequestHandler) -> None:
    """Send a 404 Not Found response."""
    send_error_response(handler, 404, "Not Found", f"Path not found: {handler.path}")
