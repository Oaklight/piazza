"""Channel route handlers — listing and detail."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._shared import send_error_response, send_json_response

if TYPE_CHECKING:
    from ..handlers import AdminRequestHandler


def handle_get_channels(handler: AdminRequestHandler) -> None:
    """Handle GET /api/channels — list all channels with stats."""
    stats = handler.bus.backend.get_stats()
    channels = []
    for bd in stats.get("channel_breakdown", []):
        ch_name = bd["channel"]
        sub_count = len(handler.bus._subs.get(ch_name, {}))
        channels.append(
            {
                "name": ch_name,
                "message_count": bd["message_count"],
                "last_message_time": bd["last_message_time"],
                "sender_count": bd["sender_count"],
                "subscription_count": sub_count,
            }
        )
    send_json_response(handler, {"channels": channels})


def handle_get_channel(handler: AdminRequestHandler, name: str) -> None:
    """Handle GET /api/channels/{name} — channel detail."""
    count = handler.bus.backend.count_messages(name)
    if count == 0:
        send_error_response(handler, 404, "Not Found", f"Channel not found: {name}")
        return

    # Get senders and msg_type distribution via query_all
    all_msgs = handler.bus.backend.query_all(channel=name, limit=10000)
    senders = sorted({m.sender for m in all_msgs})
    type_counts: dict[str, int] = {}
    last_time = ""
    for m in all_msgs:
        type_counts[m.msg_type] = type_counts.get(m.msg_type, 0) + 1
        if m.timestamp > last_time:
            last_time = m.timestamp

    msg_types = [
        {"msg_type": t, "count": c}
        for t, c in sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
    ]
    sub_count = len(handler.bus._subs.get(name, {}))

    send_json_response(
        handler,
        {
            "name": name,
            "message_count": count,
            "last_message_time": last_time,
            "senders": senders,
            "msg_types": msg_types,
            "subscription_count": sub_count,
        },
    )
