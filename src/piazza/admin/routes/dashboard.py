"""Dashboard route handlers — stats and throughput."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, TypedDict

from ._shared import send_json_response

if TYPE_CHECKING:
    from ..handlers import AdminRequestHandler


class _ThroughputBucket(TypedDict):
    """Single bucket in the per-second throughput histogram."""

    timestamp: str
    count: int


def handle_get_stats(handler: AdminRequestHandler) -> None:
    """Handle GET /api/stats — dashboard aggregates."""
    stats = handler.bus.backend.get_stats()

    # Add subscription count from bus
    sub_info = handler.bus.subscription_counts()
    stats["active_subscriptions"] = sum(len(ids) for ids in sub_info.values())

    send_json_response(handler, stats)


def handle_get_throughput(handler: AdminRequestHandler) -> None:
    """Handle GET /api/stats/throughput — per-second message counts."""
    timestamps = handler.bus.backend.query_recent_timestamps(60)

    now = datetime.now(timezone.utc)
    buckets: list[_ThroughputBucket] = []
    for i in range(60):
        bucket_time = now - timedelta(seconds=59 - i)
        bucket_iso = bucket_time.replace(microsecond=0).isoformat()
        buckets.append({"timestamp": bucket_iso, "count": 0})

    # Bucket timestamps by second
    for ts_str in timestamps:
        try:
            ts = datetime.fromisoformat(ts_str)
            delta = (now - ts).total_seconds()
            if 0 <= delta < 60:
                idx = 59 - int(delta)
                if 0 <= idx < 60:
                    buckets[idx]["count"] += 1
        except (ValueError, IndexError):
            continue

    send_json_response(handler, {"window_seconds": 60, "buckets": buckets})
