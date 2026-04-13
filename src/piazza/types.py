"""Core types for piazza message bus."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Protocol


@dataclass(frozen=True, slots=True)
class Message:
    """A message in the bus.

    Attributes:
        id: Unique message identifier (UUID v7, time-ordered).
        channel: Channel name this message belongs to.
        sender: Identifier of the sending agent.
        msg_type: Application-defined message type
            (e.g. "text", "context_sync", "notification", "artifact").
        payload: Message content. JSON string or plain text.
        timestamp: ISO 8601 timestamp of when the message was published.
        metadata: Optional extra fields as a dict.
    """

    id: str
    channel: str
    sender: str
    msg_type: str
    payload: str
    timestamp: str
    metadata: dict | None = field(default=None)

    def payload_json(self) -> dict:
        """Parse payload as JSON. Raises ValueError if not valid JSON."""
        return json.loads(self.payload)


class MessageBus(Protocol):
    """Protocol for a message bus implementation."""

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
        ...

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
                Used as a cursor for incremental polling.
            limit: Maximum number of messages to return.

        Returns:
            Messages in chronological order (oldest first).
        """
        ...

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
        ...

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription.

        Args:
            subscription_id: ID returned by subscribe().
        """
        ...

    def channels(self) -> list[str]:
        """List all channels that have at least one message.

        Returns:
            Sorted list of channel names.
        """
        ...


class StorageBackend(Protocol):
    """Protocol for message storage backends.

    Handles persistence and retrieval of messages. Implementations
    can use SQLite, Redis, in-memory storage, etc.
    """

    def store(self, message: Message) -> None:
        """Persist a message."""
        ...

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
        ...

    def list_channels(self) -> list[str]:
        """List all channels that have at least one message.

        Returns:
            Sorted list of channel names.
        """
        ...

    def close(self) -> None:
        """Release resources held by this backend."""
        ...


class Serializer(Protocol):
    """Protocol for message payload serialization.

    Handles encoding/decoding of message metadata and payload content.
    Implementations can use JSON, MessagePack, Protobuf, etc.
    """

    def encode(self, obj: dict) -> str:
        """Encode a dict to string representation.

        Args:
            obj: Dictionary to encode.

        Returns:
            Encoded string.
        """
        ...

    def decode(self, data: str) -> dict:
        """Decode a string back to dict.

        Args:
            data: Encoded string.

        Returns:
            Decoded dictionary.
        """
        ...
