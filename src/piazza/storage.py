"""Storage backend implementations for piazza message bus."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
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


class SQLiteStorage:
    """SQLite-backed storage.

    Supports cross-process sharing via WAL mode.

    Args:
        db_path: Path to SQLite database file. Use ":memory:" for
            ephemeral storage (testing).
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

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
        return f"SQLiteStorage({self._db_path!r})"


class MemoryStorage:
    """In-memory storage backend for testing.

    Messages are stored in plain Python lists. Not suitable for
    cross-process communication.
    """

    def __init__(self) -> None:
        self._messages: dict[str, list[Message]] = defaultdict(list)

    def store(self, message: Message) -> None:
        """Store a message in memory.

        Args:
            message: Message to store.
        """
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
        msgs = self._messages.get(channel, [])
        if after:
            msgs = [m for m in msgs if m.id > after]
        return msgs[:limit]

    def list_channels(self) -> list[str]:
        """List all channels that have at least one message.

        Returns:
            Sorted list of channel names.
        """
        return sorted(ch for ch, msgs in self._messages.items() if msgs)

    def close(self) -> None:
        """Clear all stored messages."""
        self._messages.clear()

    def __repr__(self) -> str:
        return "MemoryStorage()"
