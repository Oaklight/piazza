"""In-memory message backend for piazza bus."""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from piazza.types import ClaimResult, Message


class MemoryBackend:
    """In-memory message backend for testing.

    Messages are stored in plain Python lists, protected by a lock for
    thread safety. Not suitable for cross-process communication.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: dict[str, list[Message]] = defaultdict(list)
        self._queue_status: dict[str, dict] = {}

    def store(self, message: Message, *, queue: bool = False) -> None:
        """Store a message in memory.

        Args:
            message: Message to store.
            queue: If True, mark as claimable.
        """
        with self._lock:
            self._messages[message.channel].append(message)
            if queue:
                self._queue_status[message.id] = {
                    "status": "unclaimed",
                    "claimed_by": None,
                    "claimed_at": None,
                }

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

    def claim(self, channel: str, claimed_by: str) -> ClaimResult | None:
        claimed_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            for msg in self._messages.get(channel, []):
                qs = self._queue_status.get(msg.id)
                if qs and qs["status"] == "unclaimed":
                    qs["status"] = "claimed"
                    qs["claimed_by"] = claimed_by
                    qs["claimed_at"] = claimed_at
                    return ClaimResult(
                        message=msg,
                        status="claimed",
                        claimed_by=claimed_by,
                        claimed_at=claimed_at,
                    )
        return None

    def ack(self, message_id: str, claimed_by: str) -> ClaimResult | None:
        with self._lock:
            qs = self._queue_status.get(message_id)
            if not qs or qs["status"] != "claimed" or qs["claimed_by"] != claimed_by:
                return None
            qs["status"] = "completed"
            for msgs in self._messages.values():
                for msg in msgs:
                    if msg.id == message_id:
                        return ClaimResult(
                            message=msg,
                            status="completed",
                            claimed_by=qs["claimed_by"],
                            claimed_at=qs["claimed_at"],
                        )
        return None

    def get_queue_stats(self, channel: str | None = None) -> dict:
        result = {"unclaimed": 0, "claimed": 0, "completed": 0}
        with self._lock:
            if channel:
                ids = {m.id for m in self._messages.get(channel, [])}
                for mid, qs in self._queue_status.items():
                    if mid in ids and qs["status"] in result:
                        result[qs["status"]] += 1
            else:
                for qs in self._queue_status.values():
                    if qs["status"] in result:
                        result[qs["status"]] += 1
        return result

    def retire_completed(self, max_age_seconds: int = 86400, max_per_channel: int = 1000) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat()
        with self._lock:
            to_remove = self._collect_retire_ids(cutoff, max_per_channel)
            return self._remove_messages(to_remove)

    def _collect_retire_ids(self, cutoff: str, max_per_channel: int) -> set[str]:
        to_remove: set[str] = set()
        for mid, qs in self._queue_status.items():
            if qs["status"] == "completed" and qs["claimed_at"] and qs["claimed_at"] < cutoff:
                to_remove.add(mid)
        per_channel: dict[str, list[str]] = defaultdict(list)
        for ch, msgs in self._messages.items():
            for msg in msgs:
                qs = self._queue_status.get(msg.id)
                if qs and qs["status"] == "completed" and msg.id not in to_remove:
                    per_channel[ch].append(msg.id)
        for _ch, ids in per_channel.items():
            if len(ids) > max_per_channel:
                to_remove.update(ids[: len(ids) - max_per_channel])
        return to_remove

    def _remove_messages(self, to_remove: set[str]) -> int:
        deleted = 0
        for ch in list(self._messages):
            before = len(self._messages[ch])
            self._messages[ch] = [m for m in self._messages[ch] if m.id not in to_remove]
            deleted += before - len(self._messages[ch])
        for mid in to_remove:
            self._queue_status.pop(mid, None)
        return deleted

    def get_backend_info(self) -> dict:
        """Return backend type and usage info."""
        import sys

        with self._lock:
            total_msgs = sum(len(msgs) for msgs in self._messages.values())
            n_channels = len(self._messages)
            size_bytes = sys.getsizeof(self._messages)
            for msgs in self._messages.values():
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
            self._queue_status.clear()

    def __repr__(self) -> str:
        return "MemoryBackend()"
