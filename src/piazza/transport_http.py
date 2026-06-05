"""HTTP transport for PiazzaClient — connects to an HttpFrontend server.

Uses vendored zerodep modules:
- ``_vendor.httpclient`` for HTTP requests
- ``_vendor.sse`` for SSE streaming with auto-reconnect
"""

from __future__ import annotations

import logging
import threading
import urllib.parse
from collections.abc import Callable
from typing import Any

from piazza._vendor.httpclient import Client as HttpClient
from piazza._vendor.httpclient import get as http_get
from piazza._vendor.httpclient import post as http_post
from piazza._vendor.sse import SSEClient
from piazza.types import Message

logger = logging.getLogger(__name__)


class HttpTransport:
    """Client-side transport that talks to a remote HttpFrontend.

    Implements the Transport protocol for PiazzaClient, enabling
    remote agent communication over HTTP.

    Args:
        base_url: Server URL, e.g. "http://localhost:8741".
        agent_id: Agent identifier for SSE subscriptions.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        agent_id: str = "",
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._agent_id = agent_id
        self._timeout = timeout

        # SSE state
        self._sse_thread: threading.Thread | None = None
        self._sse_channels: set[str] = set()
        self._sse_callbacks: dict[str, dict[str, Callable]] = {}  # channel -> {sub_id: cb}
        self._sse_lock = threading.Lock()
        self._sse_stop = threading.Event()
        self._sse_client: SSEClient | None = None
        self._sub_counter = 0

        # Cache server auth requirement
        self._require_auth: bool | None = None

    # ── Transport Protocol ────────────────────────────────────────

    def publish(
        self,
        channel: str,
        sender: str,
        msg_type: str,
        payload: str,
        metadata: dict | None = None,
    ) -> str:
        """Publish a message via the remote server."""
        body: dict[str, Any] = {
            "channel": channel,
            "sender": sender,
            "msg_type": msg_type,
            "payload": payload,
        }
        if metadata:
            body["metadata"] = metadata

        resp = http_post(
            f"{self._base_url}/v1/publish",
            json=body,
            timeout=self._timeout,
        )
        return resp.json()["message_id"]

    def query(
        self,
        channel: str,
        after: str | None = None,
        limit: int = 100,
    ) -> list[Message]:
        """Query messages from a channel via the remote server."""
        params: dict[str, str] = {"channel": channel, "limit": str(limit)}
        if after:
            params["after"] = after

        url = f"{self._base_url}/v1/query?{urllib.parse.urlencode(params)}"
        resp = http_get(url, timeout=self._timeout)
        return [self._dict_to_msg(m) for m in resp.json().get("messages", [])]

    def list_channels(self) -> list[str]:
        """List all channels on the remote server."""
        resp = http_get(f"{self._base_url}/v1/channels", timeout=self._timeout)
        return resp.json().get("channels", [])

    @property
    def require_auth(self) -> bool:
        """Whether the remote bus requires authentication."""
        if self._require_auth is None:
            resp = http_get(f"{self._base_url}/v1/auth/check", timeout=self._timeout)
            self._require_auth = resp.json().get("require_auth", False)
        return self._require_auth

    def close(self) -> None:
        """Stop SSE thread and release resources."""
        self._sse_stop.set()
        if self._sse_client:
            self._sse_client.close()
        if self._sse_thread and self._sse_thread.is_alive():
            self._sse_thread.join(timeout=3)

    # ── SSE Subscription ─────────────────────────────────────────

    def subscribe(
        self,
        channel: str,
        callback: Callable[[Message], None],
    ) -> str:
        """Subscribe to real-time notifications via SSE.

        Args:
            channel: Channel to subscribe to.
            callback: Function called with each new Message.

        Returns:
            Subscription ID for unsubscribe().
        """
        with self._sse_lock:
            self._sub_counter += 1
            sub_id = f"http-sub-{self._sub_counter}"

            if channel not in self._sse_callbacks:
                self._sse_callbacks[channel] = {}
            self._sse_callbacks[channel][sub_id] = callback

            needs_restart = channel not in self._sse_channels
            self._sse_channels.add(channel)

        if needs_restart:
            self._restart_sse()

        return sub_id

    def unsubscribe(self, sub_id: str) -> None:
        """Remove a subscription."""
        with self._sse_lock:
            for channel, subs in list(self._sse_callbacks.items()):
                if sub_id in subs:
                    del subs[sub_id]
                    if not subs:
                        del self._sse_callbacks[channel]
                        self._sse_channels.discard(channel)
                    break

    # ── SSE Background Thread ─────────────────────────────────────

    def _restart_sse(self) -> None:
        """(Re)start the SSE background thread with current channels."""
        self._sse_stop.set()
        if self._sse_client:
            self._sse_client.close()
        if self._sse_thread and self._sse_thread.is_alive():
            self._sse_thread.join(timeout=3)

        self._sse_stop = threading.Event()
        self._sse_thread = threading.Thread(
            target=self._sse_loop, daemon=True, name="piazza-sse"
        )
        self._sse_thread.start()

    def _sse_loop(self) -> None:
        """Background loop: connect to SSE endpoint, dispatch events."""
        while not self._sse_stop.is_set():
            with self._sse_lock:
                channels = list(self._sse_channels)
            if not channels:
                self._sse_stop.wait(1)
                continue

            url = self._build_sse_url(channels)

            try:
                self._sse_client = SSEClient(
                    url,
                    timeout=30,
                    retry_interval=2000,
                    max_retries=-1,  # unlimited reconnect
                )

                for event in self._sse_client:
                    if self._sse_stop.is_set():
                        break
                    self._dispatch_sse_event(event)

            except Exception:
                if not self._sse_stop.is_set():
                    logger.debug("SSE connection lost, reconnecting", exc_info=True)
                    self._sse_stop.wait(2)
            finally:
                if self._sse_client:
                    self._sse_client.close()
                    self._sse_client = None

    def _build_sse_url(self, channels: list[str]) -> str:
        """Build the SSE subscribe URL with channel params."""
        params = urllib.parse.urlencode(
            [("channel", ch) for ch in channels]
            + [("agent_id", self._agent_id)]
        )
        return f"{self._base_url}/v1/subscribe?{params}"

    def _dispatch_sse_event(self, event: Any) -> None:
        """Parse and dispatch a single SSE event."""
        if not event.data:
            return

        try:
            import json
            data = json.loads(event.data)
        except (ValueError, TypeError):
            return

        channel = data.get("channel")
        msg_data = data.get("message")
        if not channel or not msg_data:
            return

        msg = self._dict_to_msg(msg_data)

        with self._sse_lock:
            cbs = list((self._sse_callbacks.get(channel) or {}).values())

        for cb in cbs:
            try:
                cb(msg)
            except Exception:
                logger.debug("Subscriber callback error", exc_info=True)

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _dict_to_msg(d: dict) -> Message:
        return Message(
            id=d["id"],
            channel=d["channel"],
            sender=d["sender"],
            msg_type=d["msg_type"],
            payload=d["payload"],
            timestamp=d["timestamp"],
            metadata=d.get("metadata"),
        )

    def __repr__(self) -> str:
        return f"HttpTransport({self._base_url!r})"
