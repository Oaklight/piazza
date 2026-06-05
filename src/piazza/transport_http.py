"""HTTP transport for PiazzaClient — connects to an HttpFrontend server.

Zero external dependencies — uses urllib.request for HTTP calls and
a background thread + http.client for SSE streaming.
"""

from __future__ import annotations

import http.client
import json
import queue
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from piazza.types import Message


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
        """Publish a message via the remote server.

        Returns:
            The message ID assigned by the server.
        """
        body = {
            "channel": channel,
            "sender": sender,
            "msg_type": msg_type,
            "payload": payload,
        }
        if metadata:
            body["metadata"] = metadata

        resp = self._post("/v1/publish", body)
        return resp["message_id"]

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

        resp = self._get("/v1/query", params)
        return [self._dict_to_msg(m) for m in resp.get("messages", [])]

    def list_channels(self) -> list[str]:
        """List all channels on the remote server."""
        resp = self._get("/v1/channels")
        return resp.get("channels", [])

    @property
    def require_auth(self) -> bool:
        """Whether the remote bus requires authentication."""
        if self._require_auth is None:
            resp = self._get("/v1/auth/check")
            self._require_auth = resp.get("require_auth", False)
        return self._require_auth

    def close(self) -> None:
        """Stop SSE thread and release resources."""
        self._sse_stop.set()
        if self._sse_thread and self._sse_thread.is_alive():
            self._sse_thread.join(timeout=3)

    # ── SSE Subscription (bonus, not in Transport protocol) ───────

    def subscribe(
        self,
        channel: str,
        callback: Callable[[Message], None],
    ) -> str:
        """Subscribe to real-time notifications via SSE.

        The callback fires in the SSE background thread when a
        new message arrives on the channel.

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
        """Remove a subscription.

        Args:
            sub_id: ID returned by subscribe().
        """
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
        if self._sse_thread and self._sse_thread.is_alive():
            self._sse_thread.join(timeout=3)

        self._sse_stop = threading.Event()
        self._sse_thread = threading.Thread(
            target=self._sse_loop, daemon=True, name="piazza-sse"
        )
        self._sse_thread.start()

    def _sse_loop(self) -> None:
        """Background loop: connect to SSE endpoint, dispatch events."""
        parsed = urllib.parse.urlparse(self._base_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 80

        while not self._sse_stop.is_set():
            try:
                with self._sse_lock:
                    channels = list(self._sse_channels)
                if not channels:
                    self._sse_stop.wait(1)
                    continue

                params = urllib.parse.urlencode(
                    [("channel", ch) for ch in channels]
                    + [("agent_id", self._agent_id)]
                )
                path = f"/v1/subscribe?{params}"

                conn = http.client.HTTPConnection(host, port, timeout=30)
                conn.request("GET", path)
                resp = conn.getresponse()

                if resp.status != 200:
                    conn.close()
                    self._sse_stop.wait(2)
                    continue

                # Read SSE stream line by line
                buf = ""
                while not self._sse_stop.is_set():
                    line = resp.readline()
                    if not line:
                        break  # connection closed
                    decoded = line.decode("utf-8", errors="replace")
                    if decoded.strip() == "":
                        # Empty line = event boundary
                        if buf.strip():
                            self._dispatch_sse_event(buf)
                        buf = ""
                    else:
                        buf += decoded

                conn.close()

            except (OSError, http.client.HTTPException):
                if not self._sse_stop.is_set():
                    self._sse_stop.wait(2)  # reconnect delay

    def _dispatch_sse_event(self, raw: str) -> None:
        """Parse and dispatch a single SSE event."""
        data_lines = []
        for line in raw.split("\n"):
            if line.startswith("data: "):
                data_lines.append(line[6:])
            elif line.startswith(":"):
                pass  # comment / keepalive

        if not data_lines:
            return

        try:
            event = json.loads("".join(data_lines))
        except json.JSONDecodeError:
            return

        channel = event.get("channel")
        msg_data = event.get("message")
        if not channel or not msg_data:
            return

        msg = self._dict_to_msg(msg_data)

        with self._sse_lock:
            cbs = list((self._sse_callbacks.get(channel) or {}).values())

        for cb in cbs:
            try:
                cb(msg)
            except Exception:
                pass  # don't let subscriber errors kill the SSE loop

    # ── HTTP Helpers ──────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict:
        url = f"{self._base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read())

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self._base_url}{path}"
        data = json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read())

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
