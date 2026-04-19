"""Message bus implementation."""

from __future__ import annotations

import threading
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from piazza.backends import SQLiteBackend
from piazza.protocols import Backend
from piazza.serializers import JSONSerializer
from piazza.types import Message

if TYPE_CHECKING:
    from piazza.admin.server import AdminInfo
    from piazza.protocols import Serializer

# Thread-safe monotonic sequence for _uuid7 fallback
_seq_lock = threading.Lock()
_seq_last_ms = 0
_seq_counter = 0


def _uuid7() -> str:
    """Generate a UUID v7 (time-ordered) as string.

    Falls back to a time-sortable synthetic ID on Python < 3.14.
    Uses a per-millisecond sequence counter to guarantee strict
    lexicographic ordering even within the same millisecond.
    """
    try:
        return str(uuid.uuid7())
    except AttributeError:
        global _seq_last_ms, _seq_counter  # noqa: PLW0603
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with _seq_lock:
            if ts_ms == _seq_last_ms:
                _seq_counter += 1
            else:
                _seq_last_ms = ts_ms
                _seq_counter = 0
            seq = _seq_counter
        ts_hex = f"{ts_ms:012x}"
        seq_hex = f"{seq:04x}"
        rand = uuid.uuid4().hex[16:]
        return f"{ts_hex[:8]}-{ts_hex[8:12]}-7{seq_hex[:3]}-{seq_hex[3]}{rand[:3]}-{rand[3:15]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Bus:
    """Composable message bus.

    Combines a Backend for message transport/persistence with
    in-process pub/sub. The serializer is used for encoding/decoding
    metadata dicts.

    Args:
        backend: Message backend for transport and persistence.
            Defaults to in-memory SQLite.
        serializer: Serializer for metadata encoding.
            Defaults to JSON.

    Example:
        >>> bus = Bus()  # in-memory SQLite + JSON
        >>> bus = Bus(backend=SQLiteBackend("workspace/.piazza.db"))
        >>> bus = Bus(backend=MemoryBackend())  # pure in-memory for tests
    """

    def __init__(
        self,
        backend: Backend | None = None,
        serializer: Serializer | None = None,
    ) -> None:
        self._backend = backend or SQLiteBackend()
        self._serializer = serializer or JSONSerializer()
        self._subs: dict[str, dict[str, Callable[[Message], None]]] = defaultdict(dict)

    @property
    def backend(self) -> Backend:
        """The underlying message backend."""
        return self._backend

    @property
    def serializer(self) -> Serializer:
        """The serializer used for metadata."""
        return self._serializer

    def publish(
        self,
        channel: str,
        sender: str,
        msg_type: str,
        payload: str,
        metadata: dict | None = None,
    ) -> str:
        """Publish a message to a channel.

        Args:
            channel: Target channel name.
            sender: Agent ID of the sender.
            msg_type: Application-defined type string.
            payload: Message content.
            metadata: Optional extra fields.

        Returns:
            The message ID.
        """
        msg_id = _uuid7()
        timestamp = _now_iso()

        msg = Message(
            id=msg_id,
            channel=channel,
            sender=sender,
            msg_type=msg_type,
            payload=payload,
            timestamp=timestamp,
            metadata=metadata,
        )

        self._backend.store(msg)

        # Notify in-process subscribers
        for callback in self._subs.get(channel, {}).values():
            callback(msg)

        return msg_id

    def poll(
        self,
        channel: str,
        after: str | None = None,
        limit: int = 100,
    ) -> list[Message]:
        """Retrieve messages from a channel.

        Args:
            channel: Channel to read from.
            after: If provided, only return messages with ID greater than this.
            limit: Maximum number of messages to return.

        Returns:
            Messages in chronological order (oldest first).
        """
        return self._backend.query(channel, after=after, limit=limit)

    def subscribe(
        self,
        channel: str,
        callback: Callable[[Message], None],
    ) -> str:
        """Register an in-process callback for new messages on a channel.

        The callback is invoked synchronously during publish() within the
        same process. For cross-process notification, use poll() instead.

        Args:
            channel: Channel to watch.
            callback: Function called with each new Message.

        Returns:
            Subscription ID for use with unsubscribe().
        """
        sub_id = uuid.uuid4().hex[:8]
        self._subs[channel][sub_id] = callback
        return sub_id

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription.

        Args:
            subscription_id: ID returned by subscribe().
        """
        for channel_subs in self._subs.values():
            channel_subs.pop(subscription_id, None)

    def channels(self) -> list[str]:
        """List all channels that have at least one message.

        Returns:
            Sorted list of channel names.
        """
        return self._backend.list_channels()

    def start_admin(
        self,
        host: str = "127.0.0.1",
        port: int = 8741,
        serve_ui: bool = True,
        remote: bool = False,
        auth_token: str | None = None,
    ) -> AdminInfo:
        """Start the admin panel HTTP server.

        Args:
            host: Host to bind to. Defaults to localhost.
            port: Port to listen on. Defaults to 8741.
            serve_ui: Whether to serve the web UI.
            remote: Allow remote connections (binds to 0.0.0.0).
            auth_token: Optional auth token. Auto-generated if remote=True.

        Returns:
            AdminInfo with server URL and token.
        """
        from piazza.admin import AdminServer

        self._admin_server = AdminServer(
            self,
            host=host,
            port=port,
            serve_ui=serve_ui,
            remote=remote,
            auth_token=auth_token,
        )
        return self._admin_server.start()

    def stop_admin(self) -> None:
        """Stop the admin panel server if running."""
        if hasattr(self, "_admin_server") and self._admin_server:
            self._admin_server.stop()
            self._admin_server = None

    def close(self) -> None:
        """Release resources held by the backend."""
        self.stop_admin()
        self._backend.close()

    def __enter__(self) -> Bus:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Bus(backend={self._backend!r})"


class SQLiteBus(Bus):
    """Convenience subclass: SQLite-backed bus.

    Shorthand for ``Bus(backend=SQLiteBackend(db_path))``.

    Args:
        db_path: Path to SQLite database file. Use ":memory:" for
            ephemeral in-memory bus (testing).

    Example:
        >>> bus = SQLiteBus("workspace/.piazza.db")
        >>> msg_id = bus.publish("sync", "agent-a", "context_sync", '{"commits": ["abc"]}')
        >>> messages = bus.poll("sync")
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        super().__init__(backend=SQLiteBackend(db_path))

    def __repr__(self) -> str:
        return f"SQLiteBus({self._backend!r})"
