"""In-memory message backend for piazza bus."""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from piazza.types import Message


class MemoryBackend:
    """In-memory message backend for testing.

    Messages are stored in plain Python lists, protected by a lock for
    thread safety. Not suitable for cross-process communication.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: dict[str, list[Message]] = defaultdict(list)

    def store(self, message: Message) -> None:
        """Store a message in memory.

        Args:
            message: Message to store.
        """
        with self._lock:
            self._messages[message.channel].append(message)

    def query(
        self,
        channel: str,
        after: str | None = None,
        limit: int = 100,
    ) -> list[Message]:
        """Retrieve messages from a channel.

        Args:
            channel: Channel to query.
            after: If provided, only return messages with ID > this value.
            limit: Maximum number of messages to return.

        Returns:
            Messages in chronological order (oldest first).
        """
        with self._lock:
            msgs = list(self._messages.get(channel, []))
        if after:
            msgs = [m for m in msgs if m.id > after]
        return msgs[:limit]

    def list_channels(self) -> list[str]:
        """List all channels that have at least one message.

        Returns:
            Sorted list of channel names.
        """
        with self._lock:
            return sorted(ch for ch, msgs in self._messages.items() if msgs)

    def count_messages(self, channel: str | None = None) -> int:
        """Count messages, optionally filtered by channel.

        Args:
            channel: If provided, count only messages in this channel.

        Returns:
            Number of messages.
        """
        with self._lock:
            if channel:
                return len(self._messages.get(channel, []))
            return sum(len(msgs) for msgs in self._messages.values())

    def query_all(
        self,
        after: str | None = None,
        limit: int = 100,
        channel: str | None = None,
        sender: str | None = None,
        msg_type: str | None = None,
    ) -> list[Message]:
        """Query messages across all channels with optional filters.

        Args:
            after: Cursor for pagination (message ID).
            limit: Maximum number of messages to return.
            channel: Filter by channel name.
            sender: Filter by sender.
            msg_type: Filter by message type.

        Returns:
            Messages in chronological order (oldest first).
        """
        with self._lock:
            if channel:
                msgs = list(self._messages.get(channel, []))
            else:
                msgs = [m for ch_msgs in self._messages.values() for m in ch_msgs]
                msgs.sort(key=lambda m: m.id)

        msgs = self._apply_filters(msgs, after=after, sender=sender, msg_type=msg_type)
        return msgs[:limit]

    @staticmethod
    def _apply_filters(
        msgs: list[Message],
        after: str | None = None,
        sender: str | None = None,
        msg_type: str | None = None,
    ) -> list[Message]:
        """Apply optional filters to a message list."""
        if after:
            msgs = [m for m in msgs if m.id > after]
        if sender:
            msgs = [m for m in msgs if m.sender == sender]
        if msg_type:
            msgs = [m for m in msgs if m.msg_type == msg_type]
        return msgs

    def get_stats(self) -> dict:
        """Return aggregate statistics for admin dashboard.

        Returns:
            Dict with total_messages, total_channels, total_senders,
            channel_breakdown, and msg_type_distribution.
        """
        with self._lock:
            all_msgs = [m for ch_msgs in self._messages.values() for m in ch_msgs]
            n_channels = len(self._messages)

        senders: set[str] = set()
        types: dict[str, int] = {}
        breakdown: dict[str, dict] = {}

        for m in all_msgs:
            senders.add(m.sender)
            types[m.msg_type] = types.get(m.msg_type, 0) + 1
            if m.channel not in breakdown:
                breakdown[m.channel] = {
                    "channel": m.channel,
                    "message_count": 0,
                    "last_message_time": m.timestamp,
                    "sender_count": 0,
                    "_senders": set(),
                }
            bd = breakdown[m.channel]
            bd["message_count"] += 1
            bd["_senders"].add(m.sender)
            if m.timestamp > bd["last_message_time"]:
                bd["last_message_time"] = m.timestamp

        for bd in breakdown.values():
            bd["sender_count"] = len(bd.pop("_senders"))

        return {
            "total_messages": len(all_msgs),
            "total_channels": n_channels,
            "total_senders": len(senders),
            "channel_breakdown": sorted(
                breakdown.values(),
                key=lambda x: x["message_count"],
                reverse=True,
            ),
            "msg_type_distribution": [
                {"msg_type": t, "count": c}
                for t, c in sorted(types.items(), key=lambda x: x[1], reverse=True)
            ],
        }

    def query_recent_timestamps(self, seconds: int = 60) -> list[str]:
        """Return timestamps of messages from the last N seconds.

        Args:
            seconds: Time window in seconds.

        Returns:
            List of ISO 8601 timestamp strings, sorted ascending.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        result = []
        with self._lock:
            for msgs in self._messages.values():
                for m in msgs:
                    if m.timestamp > cutoff:
                        result.append(m.timestamp)
        result.sort()
        return result

    def get_backend_info(self) -> dict:
        """Return backend type and usage info."""
        import sys

        with self._lock:
            total_msgs = sum(len(msgs) for msgs in self._channels.values())
            n_channels = len(self._channels)
            # Rough memory estimate
            size_bytes = sys.getsizeof(self._channels)
            for msgs in self._channels.values():
                size_bytes += sys.getsizeof(msgs)
                for m in msgs:
                    size_bytes += sys.getsizeof(m)

        return {
            "type": "memory",
            "total_messages": total_msgs,
            "total_channels": n_channels,
            "estimated_size_bytes": size_bytes,
            "estimated_size_mb": round(size_bytes / (1024 * 1024), 2),
        }

    def close(self) -> None:
        """Clear all stored messages."""
        with self._lock:
            self._messages.clear()

    def __repr__(self) -> str:
        return "MemoryBackend()"
