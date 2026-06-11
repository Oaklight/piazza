"""Subscription route handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._shared import send_json_response

if TYPE_CHECKING:
    from ..handlers import AdminRequestHandler


def handle_get_subscriptions(handler: AdminRequestHandler) -> None:
    """Handle GET /api/subscriptions — list in-process subscriptions."""
    channels = []
    total = 0
    for channel, subs in sorted(handler.bus._subs.items()):
        if subs:
            sub_ids = list(subs.keys())
            channels.append(
                {
                    "channel": channel,
                    "subscription_ids": sub_ids,
                    "count": len(sub_ids),
                }
            )
            total += len(sub_ids)

    send_json_response(handler, {"total": total, "channels": channels})
