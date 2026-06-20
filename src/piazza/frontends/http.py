"""HTTP frontend for piazza — REST API + SSE push.

Exposes the Bus over HTTP so remote PiazzaClient instances can
publish, query, and receive real-time notifications via SSE.

Zero external dependencies — uses stdlib http.server + threading.

API Endpoints:
    POST /v1/publish         — publish a message
    GET  /v1/query           — query messages from a channel
    GET  /v1/channels        — list channels
    GET  /v1/subscribe       — SSE stream for real-time notifications
    GET  /v1/auth/check      — validate agent credentials
    GET  /v1/registry/lookup — look up agent registration
    GET  /health             — health check
"""

from __future__ import annotations

import contextlib
import json
import queue
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from piazza.bus import Bus


class _SseClient:
    """Tracks one SSE connection's subscriptions and message queue."""

    __slots__ = ("q", "sub_ids", "agent_id")

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.q: queue.Queue[dict | None] = queue.Queue(maxsize=256)
        self.sub_ids: list[str] = []


class _HttpHandler(BaseHTTPRequestHandler):
    """HTTP request handler for piazza remote transport API."""

    bus: Bus  # set by HttpFrontend before serving
    sse_clients: list[_SseClient]  # shared mutable list
    sse_lock: threading.Lock

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP logging."""

    # ── Routing ───────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = urllib.parse.parse_qs(parsed.query)

        routes: dict[str, Any] = {
            "/v1/query": lambda: self._handle_query(qs),
            "/v1/channels": lambda: self._handle_channels(),
            "/v1/subscribe": lambda: self._handle_subscribe(qs),
            "/v1/auth/check": lambda: self._handle_auth_check(),
            "/v1/registry/lookup": lambda: self._handle_registry_lookup(qs),
            "/health": lambda: self._json({"status": "ok"}),
        }

        handler = routes.get(path)
        if handler:
            handler()
        else:
            self._error(404, "Not Found", f"No route: {path}")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        body = self._read_body()
        if body is None:
            return  # error already sent

        if path == "/v1/publish":
            self._handle_publish(body)
        else:
            self._error(404, "Not Found", f"No route: {path}")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ── Handlers ──────────────────────────────────────────────────

    def _handle_publish(self, data: dict) -> None:
        required = ("channel", "sender", "msg_type", "payload")
        missing = [f for f in required if f not in data]
        if missing:
            self._error(400, "Bad Request", f"Missing: {', '.join(missing)}")
            return

        msg_id = self.bus.publish(
            channel=data["channel"],
            sender=data["sender"],
            msg_type=data["msg_type"],
            payload=data["payload"],
            metadata=data.get("metadata"),
        )
        self._json({"message_id": msg_id})

    def _handle_query(self, qs: dict[str, list[str]]) -> None:
        channel = self._qs_first(qs, "channel")
        if not channel:
            self._error(400, "Bad Request", "Query param 'channel' required")
            return

        after = self._qs_first(qs, "after")
        limit = int(self._qs_first(qs, "limit") or "100")

        msgs = self.bus.poll(channel, after=after, limit=limit)
        self._json(
            {
                "messages": [self._msg_to_dict(m) for m in msgs],
                "count": len(msgs),
            }
        )

    def _handle_channels(self) -> None:
        self._json({"channels": self.bus.channels()})

    def _handle_auth_check(self) -> None:
        """Return whether the bus requires auth + validate if credentials sent."""
        self._json({"require_auth": self.bus.require_auth})

    def _handle_registry_lookup(self, qs: dict[str, list[str]]) -> None:
        agent_id = self._qs_first(qs, "agent_id")
        if not agent_id:
            self._error(400, "Bad Request", "Query param 'agent_id' required")
            return

        # Search _system:registry for this agent's registration
        msgs = self.bus.poll("_system:registry", limit=1000)
        for m in reversed(msgs):
            if m.sender == agent_id and m.msg_type == "register":
                self._json({"found": True, "agent_id": agent_id, "metadata": m.metadata})
                return

        self._json({"found": False, "agent_id": agent_id})

    def _handle_subscribe(self, qs: dict[str, list[str]]) -> None:
        """SSE endpoint — streams new messages for subscribed channels.

        Query params:
            channel: one or more channel names to subscribe to
            agent_id: the subscribing agent's ID (for logging)
        """
        channels = qs.get("channel", [])
        agent_id = self._qs_first(qs, "agent_id") or "anonymous"

        if not channels:
            self._error(400, "Bad Request", "At least one 'channel' param required")
            return

        # Set up SSE response
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors()
        self.end_headers()

        client = _SseClient(agent_id)

        # Subscribe to each channel on the bus
        def _make_callback(ch: str):
            def _cb(msg):
                try:
                    client.q.put_nowait(
                        {
                            "channel": ch,
                            "message": self._msg_to_dict(msg),
                        }
                    )
                except queue.Full:
                    # Notify the slow consumer that events were dropped
                    with contextlib.suppress(queue.Full):
                        client.q.put_nowait({"_dropped": True, "channel": ch})

            return _cb

        for ch in channels:
            sub_id = self.bus.subscribe(ch, _make_callback(ch))
            client.sub_ids.append(sub_id)

        with self.sse_lock:
            self.sse_clients.append(client)

        # Stream events until client disconnects
        try:
            # Send initial keepalive
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()

            while True:
                try:
                    event = client.q.get(timeout=15)
                except queue.Empty:
                    # Send keepalive comment
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    continue

                if event is None:
                    break  # shutdown signal

                if event.get("_dropped"):
                    # Warn client about dropped events
                    try:
                        ch = event.get("channel", "?")
                        self.wfile.write(f": dropped events on {ch}\n\n".encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    continue

                data = json.dumps(event, ensure_ascii=False)
                try:
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break

        finally:
            # Cleanup subscriptions
            for sub_id in client.sub_ids:
                self.bus.unsubscribe(sub_id)
            with self.sse_lock, contextlib.suppress(ValueError):
                self.sse_clients.remove(client)

    # ── Helpers ───────────────────────────────────────────────────

    def _read_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._error(400, "Bad Request", "Empty body")
            return None
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError as e:
            self._error(400, "Bad Request", f"Invalid JSON: {e}")
            return None

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, error: str, message: str) -> None:
        self._json({"error": error, "message": message}, status)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    @staticmethod
    def _qs_first(qs: dict[str, list[str]], key: str) -> str | None:
        vals = qs.get(key, [])
        return vals[0] if vals else None

    @staticmethod
    def _msg_to_dict(m) -> dict:
        return {
            "id": m.id,
            "channel": m.channel,
            "sender": m.sender,
            "msg_type": m.msg_type,
            "payload": m.payload,
            "timestamp": m.timestamp,
            "metadata": m.metadata,
        }


class HttpFrontend:
    """HTTP frontend — REST API + SSE push notifications.

    Exposes a Bus over HTTP so remote PiazzaClient instances
    can connect via HttpTransport.

    Args:
        host: Bind address. Default "127.0.0.1".
        port: Bind port. Default 8741.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8741) -> None:
        self._host = host
        self._port = port
        self._bus: Bus | None = None
        self._server: ThreadingHTTPServer | None = None
        self._sse_clients: list[_SseClient] = []
        self._sse_lock = threading.Lock()

    def attach(self, bus: Bus) -> None:
        """Bind this frontend to a Bus.

        Args:
            bus: The Bus to expose over HTTP.

        Raises:
            RuntimeError: If already attached.
        """
        if self._bus is not None:
            raise RuntimeError("Frontend already attached to a bus")
        self._bus = bus

    def serve_forever(self) -> None:
        """Start the HTTP server. Blocks until shutdown().

        Raises:
            RuntimeError: If not attached to a Bus.
        """
        if self._bus is None:
            raise RuntimeError("Must call attach(bus) before serve_forever()")

        # Configure handler class with shared state
        handler = type(
            "_BoundHandler",
            (_HttpHandler,),
            {
                "bus": self._bus,
                "sse_clients": self._sse_clients,
                "sse_lock": self._sse_lock,
            },
        )

        self._server = ThreadingHTTPServer((self._host, self._port), handler)
        self._server.daemon_threads = True
        self._server.serve_forever()

    def shutdown(self) -> None:
        """Stop the server and disconnect all SSE clients."""
        # Signal all SSE clients to disconnect
        with self._sse_lock:
            for client in self._sse_clients:
                client.q.put(None)

        if self._server is not None:
            self._server.shutdown()
            self._server = None

    @property
    def address(self) -> tuple[str, int]:
        """Return the (host, port) this frontend is bound to.

        Returns actual bound address (useful when port=0 for OS-assigned).
        """
        if self._server is not None:
            addr = self._server.server_address
            # server_address can be (str|bytes, int) or longer tuple
            # for AF_INET6; for HttpFrontend we use AF_INET so it's
            # always (host_str, port). Cast for the type checker.
            host = addr[0] if isinstance(addr[0], str) else addr[0].decode()
            return (host, int(addr[1]))
        return (self._host, self._port)

    def __repr__(self) -> str:
        addr = f"{self._host}:{self._port}"
        status = "attached" if self._bus else "detached"
        return f"HttpFrontend({addr}, {status})"
