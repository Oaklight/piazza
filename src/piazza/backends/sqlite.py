"""SQLite message backend for piazza bus."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from piazza.types import Message

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    sender TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_channel_id ON messages (channel, id);
CREATE INDEX IF NOT EXISTS idx_channel_ts ON messages (channel, timestamp);
"""


class SQLiteBackend:
    """SQLite-backed message backend.

    Supports cross-process sharing via WAL mode.

    Args:
        db_path: Path to SQLite database file. Use ":memory:" for
            ephemeral storage (testing).
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Set busy_timeout BEFORE any other PRAGMA so subsequent
        # statements wait for locks instead of failing immediately.
        self._conn.execute("PRAGMA busy_timeout=5000")
        # Switching to WAL journal_mode requires an exclusive lock.
        # SQLite's busy handler does NOT cover PRAGMA journal_mode
        # mutations -- the lock contention surfaces as an immediate
        # OperationalError("database is locked"). Retry with backoff
        # so multiple processes can cold-start the same DB without
        # racing each other.
        self._enable_wal_with_retry()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _enable_wal_with_retry(
        self,
        attempts: int = 50,
        base_delay: float = 0.05,
    ) -> None:
        """Enable WAL journal mode, retrying on transient lock errors.

        Args:
            attempts: Maximum number of attempts before giving up.
            base_delay: Base seconds to wait between attempts; actual
                wait is base_delay * attempt_number (linear backoff).
        """
        for i in range(1, attempts + 1):
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or i == attempts:
                    raise
                time.sleep(base_delay * i)

    def store(self, message: Message) -> None:
        """Persist a message to SQLite.

        Args:
            message: Message to store.
        """
        meta_json = json.dumps(message.metadata) if message.metadata else None
        self._conn.execute(
            "INSERT INTO messages (id, channel, sender, msg_type, payload, timestamp, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                message.id,
                message.channel,
                message.sender,
                message.msg_type,
                message.payload,
                message.timestamp,
                meta_json,
            ),
        )
        self._conn.commit()

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
        if after:
            cursor = self._conn.execute(
                "SELECT * FROM messages WHERE channel = ? AND id > ? ORDER BY id ASC LIMIT ?",
                (channel, after, limit),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM messages WHERE channel = ? ORDER BY id ASC LIMIT ?",
                (channel, limit),
            )
        return [self._row_to_message(row) for row in cursor.fetchall()]

    def list_channels(self) -> list[str]:
        """List all channels that have at least one message.

        Returns:
            Sorted list of channel names.
        """
        cursor = self._conn.execute("SELECT DISTINCT channel FROM messages ORDER BY channel")
        return [row["channel"] for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the SQLite database connection."""
        self._conn.close()

    def count_messages(self, channel: str | None = None) -> int:
        """Count messages, optionally filtered by channel.

        Args:
            channel: If provided, count only messages in this channel.

        Returns:
            Number of messages.
        """
        if channel:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE channel = ?", (channel,)
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        return row[0]

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
        clauses: list[str] = []
        params: list = []
        if channel:
            clauses.append("channel = ?")
            params.append(channel)
        if sender:
            clauses.append("sender = ?")
            params.append(sender)
        if msg_type:
            clauses.append("msg_type = ?")
            params.append(msg_type)
        if after:
            clauses.append("id > ?")
            params.append(after)

        where = " AND ".join(clauses) if clauses else "1=1"
        params.append(limit)
        cursor = self._conn.execute(
            f"SELECT * FROM messages WHERE {where} ORDER BY id ASC LIMIT ?",
            params,
        )
        return [self._row_to_message(row) for row in cursor.fetchall()]

    def get_stats(self) -> dict:
        """Return aggregate statistics for admin dashboard.

        Returns:
            Dict with total_messages, total_channels, total_senders,
            channel_breakdown, and msg_type_distribution.
        """
        total = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        channels = self._conn.execute("SELECT COUNT(DISTINCT channel) FROM messages").fetchone()[0]
        senders = self._conn.execute("SELECT COUNT(DISTINCT sender) FROM messages").fetchone()[0]

        breakdown = []
        for row in self._conn.execute(
            "SELECT channel, COUNT(*) as cnt, MAX(timestamp) as last_ts, "
            "COUNT(DISTINCT sender) as scnt "
            "FROM messages GROUP BY channel ORDER BY cnt DESC"
        ).fetchall():
            breakdown.append(
                {
                    "channel": row[0],
                    "message_count": row[1],
                    "last_message_time": row[2],
                    "sender_count": row[3],
                }
            )

        types = []
        for row in self._conn.execute(
            "SELECT msg_type, COUNT(*) as cnt FROM messages GROUP BY msg_type ORDER BY cnt DESC"
        ).fetchall():
            types.append({"msg_type": row[0], "count": row[1]})

        return {
            "total_messages": total,
            "total_channels": channels,
            "total_senders": senders,
            "channel_breakdown": breakdown,
            "msg_type_distribution": types,
        }

    def query_recent_timestamps(self, seconds: int = 60) -> list[str]:
        """Return timestamps of messages from the last N seconds.

        Args:
            seconds: Time window in seconds.

        Returns:
            List of ISO 8601 timestamp strings, sorted ascending.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        cursor = self._conn.execute(
            "SELECT timestamp FROM messages WHERE timestamp > ? ORDER BY timestamp ASC",
            (cutoff,),
        )
        return [row[0] for row in cursor.fetchall()]

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> Message:
        """Convert a database row to a Message instance.

        Args:
            row: SQLite row with message columns.

        Returns:
            Corresponding Message object.
        """
        meta_raw = row["metadata"]
        metadata = json.loads(meta_raw) if meta_raw else None
        return Message(
            id=row["id"],
            channel=row["channel"],
            sender=row["sender"],
            msg_type=row["msg_type"],
            payload=row["payload"],
            timestamp=row["timestamp"],
            metadata=metadata,
        )

    def __repr__(self) -> str:
        return f"SQLiteBackend({self._db_path!r})"
