"""Message route handlers — query and publish."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ._shared import send_error_response, send_json_response

if TYPE_CHECKING:
    from ..handlers import AdminRequestHandler


def handle_get_messages(handler: AdminRequestHandler, query: dict[str, list[str]]) -> None:
    """Handle GET /api/messages — query messages with filters."""
    channel_list = query.get("channel", [])
    if not channel_list or not channel_list[0]:
        send_error_response(handler, 400, "Bad Request", "Query parameter 'channel' is required")
        return

    channel = channel_list[0]
    after = query.get("after", [None])[0]
    try:
        limit = int(query.get("limit", ["100"])[0])
    except (ValueError, TypeError):
        send_error_response(handler, 400, "Bad Request", "Invalid 'limit' parameter")
        return
    limit = max(1, min(limit, 500))
    sender = query.get("sender", [None])[0]
    msg_type = query.get("msg_type", [None])[0]

    if sender or msg_type:
        messages = handler.bus.backend.query_all(
            after=after,
            limit=limit,
            channel=channel,
            sender=sender,
            msg_type=msg_type,
        )
    else:
        messages = handler.bus.poll(channel, after=after, limit=limit)

    msgs_data = [
        {
            "id": m.id,
            "channel": m.channel,
            "sender": m.sender,
            "msg_type": m.msg_type,
            "payload": m.payload,
            "timestamp": m.timestamp,
            "metadata": m.metadata,
        }
        for m in messages
    ]

    send_json_response(
        handler,
        {
            "messages": msgs_data,
            "count": len(msgs_data),
            "has_more": len(msgs_data) == limit,
        },
    )


def handle_publish_message(handler: AdminRequestHandler, body: bytes) -> None:
    """Handle POST /api/messages — publish a new message."""
    if not body:
        send_error_response(handler, 400, "Bad Request", "Request body is required")
        return

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        send_error_response(handler, 400, "Bad Request", f"Invalid JSON: {e}")
        return

    required = ["channel", "sender", "msg_type", "payload"]
    missing = [f for f in required if f not in data]
    if missing:
        send_error_response(
            handler,
            400,
            "Bad Request",
            f"Missing required fields: {', '.join(missing)}",
        )
        return

    msg_id = handler.bus.publish(
        channel=data["channel"],
        sender=data["sender"],
        msg_type=data["msg_type"],
        payload=data["payload"],
        metadata=data.get("metadata"),
    )

    send_json_response(handler, {"success": True, "message_id": msg_id})
