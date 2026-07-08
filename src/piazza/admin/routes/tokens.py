"""Token management route handlers for admin panel."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ._shared import send_error_response, send_json_response

if TYPE_CHECKING:
    from ..handlers import AdminRequestHandler


def handle_list_tokens(handler: AdminRequestHandler) -> None:
    """GET /api/tokens — list all tokens (no secret values)."""
    if handler.token_store is None:
        send_json_response(handler, {"tokens": [], "enabled": False})
        return
    tokens = handler.token_store.list_tokens()
    send_json_response(handler, {"tokens": tokens, "enabled": True})


def handle_create_token(handler: AdminRequestHandler, body: bytes) -> None:
    """POST /api/tokens — create a new agent token.

    Body: ``{"agent_id": "agent-alice", "label": "description"}``
    agent_id may be null for a supertoken.
    """
    if handler.token_store is None:
        send_error_response(handler, 503, "Service Unavailable", "Token store not configured")
        return

    try:
        data = json.loads(body) if body else {}
    except (json.JSONDecodeError, ValueError):
        send_error_response(handler, 400, "Bad Request", "Invalid JSON body")
        return

    agent_id = data.get("agent_id")  # None = supertoken
    label = data.get("label", "")

    entry = handler.token_store.create_token(agent_id=agent_id, label=label)
    send_json_response(handler, {"ok": True, "token": entry}, 201)


def handle_delete_token(handler: AdminRequestHandler, token_id: str) -> None:
    """DELETE /api/tokens/{id} — delete a token."""
    if handler.token_store is None:
        send_error_response(handler, 503, "Service Unavailable", "Token store not configured")
        return

    if handler.token_store.delete_token(token_id):
        send_json_response(handler, {"ok": True, "deleted": token_id})
    else:
        send_error_response(handler, 404, "Not Found", f"Token '{token_id}' not found")


def handle_rotate_token(handler: AdminRequestHandler, token_id: str) -> None:
    """POST /api/tokens/{id}/rotate — rotate a token value."""
    if handler.token_store is None:
        send_error_response(handler, 503, "Service Unavailable", "Token store not configured")
        return

    result = handler.token_store.rotate_token(token_id)
    if result is None:
        send_error_response(handler, 404, "Not Found", f"Token '{token_id}' not found")
    else:
        send_json_response(handler, {"ok": True, "token": result})
