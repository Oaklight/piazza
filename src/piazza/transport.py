"""Transport abstraction for PiazzaClient.

Transport is an internal protocol that decouples PiazzaClient from
whether the Bus is in-process or behind a network API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from piazza.bus import Bus
    from piazza.types import Message


class Transport(Protocol):
    """Internal protocol for client-to-bus communication."""

    def publish(
        self,
        channel: str,
        sender: str,
        msg_type: str,
        payload: str,
        metadata: dict | None = None,
    ) -> str:
        """Publish a message. Returns message ID."""
        ...

    def query(
        self,
        channel: str,
        after: str | None = None,
        limit: int = 100,
    ) -> list[Message]:
        """Query messages from a channel."""
        ...

    def list_channels(self) -> list[str]:
        """List all channels with messages."""
        ...

    @property
    def require_auth(self) -> bool:
        """Whether the underlying bus requires authentication."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


class LocalTransport:
    """Transport that wraps an in-process Bus object.

    Args:
        bus: The Bus instance to delegate to.
    """

    def __init__(self, bus: Bus) -> None:
        self._bus = bus

    def publish(
        self,
        channel: str,
        sender: str,
        msg_type: str,
        payload: str,
        metadata: dict | None = None,
    ) -> str:
        """Publish via the local bus."""
        return self._bus.publish(channel, sender, msg_type, payload, metadata)

    def query(
        self,
        channel: str,
        after: str | None = None,
        limit: int = 100,
    ) -> list[Message]:
        """Query via the local bus."""
        return self._bus.poll(channel, after=after, limit=limit)

    def list_channels(self) -> list[str]:
        """List channels via the local bus."""
        return self._bus.channels()

    @property
    def require_auth(self) -> bool:
        """Delegate to bus.require_auth."""
        return self._bus.require_auth

    def close(self) -> None:
        """No-op -- caller manages bus lifecycle."""

    def __repr__(self) -> str:
        return f"LocalTransport({self._bus!r})"
