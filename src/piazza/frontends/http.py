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

Authentication:
    When a TokenStore is configured, all /v1/* endpoints (except
    /health and /v1/auth/check) require ``Authorization: Bearer pzt-...``.
    The token is validated server-side, and the sender field in publish
    requests must match the token's agent_id. Supertokens (agent_id=NULL)
    bypass sender checks.
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
    from piazza.token_store import TokenStore

# Default limits
_DEFAULT_MAX_BODY_BYTES = 1_048_576  # 1 MB
_DEFAULT_MAX_QUERY_LIMIT = 10_000

# Paths that skip token auth
_PUBLIC_PATHS = frozenset({"/health", "/v1/auth/check"})


def _agent_involved(agent_id: str, channel: str, sender: str) -> bool:
    """Check if an agent is involved in a message (sender or channel member).

    Uses exact segment matching on ``:``-delimited channel names to avoid
    substring false positives (e.g., "bob" must not match "bobby").

    Piazza channel naming conventions:
    - ``dm:agent-a:agent-b`` — direct messages
    - ``_system:registry`` — system channels
    - ``general`` — plain channel names
    """
    if sender == agent_id:
        return True
    # Check exact segment match in :-delimited channel name
    return agent_id in channel.split(":")


class _AtomicCounter:
    """Lock-free counter using list-swap for cross-thread drain.

    Uses a single-element list as the backing store so that
    ``drain()`` can atomically swap the entire list reference
    (a single ``STORE_ATTR`` under CPython's GIL) while producers
    keep incrementing the old list object — no increments are lost.

    This avoids a threading.Lock on the hot ``increment()`` path
    (called from every Bus publish callback) while keeping the
    cold ``drain()`` path (called once per 15s keepalive) race-free.
    """

    __slots__ = ("_cell",)

    def __init__(self) -> None:
        self._cell: list[int] = [0]

    def increment(self) -> None:
        """Bump the counter (called from publisher threads)."""
        self._cell[0] += 1

    def drain(self) -> int:
        """Return current count and reset to zero, atomically.

        The swap is a single STORE_ATTR; any concurrent ``increment()``
        that already loaded the old list reference writes to the old
        list, which we still read via the local variable.
        """
        old = self._cell
        self._cell = [0]
        return old[0]


class _SseClient:
    """Tracks one SSE connection's subscriptions and message queue."""

    __slots__ = ("q", "sub_ids", "agent_id", "dropped")

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.q: queue.Queue[dict | None] = queue.Queue(maxsize=256)
        self.sub_ids: list[str] = []
        self.dropped = _AtomicCounter()


class _HttpHandler(BaseHTTPRequestHandler):
    """HTTP request handler for piazza remote transport API."""

    bus: Bus  # set by HttpFrontend before serving
    token_store: TokenStore | None  # set by HttpFrontend; None = no auth
    sse_clients: list[_SseClient]  # shared mutable list
    sse_lock: threading.Lock
    cors_origin: str  # set by HttpFrontend
    max_body_bytes: int  # set by HttpFrontend
    max_query_limit: int  # set by HttpFrontend

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP logging."""

    # ── Auth ──────────────────────────────────────────────────────

    def _check_auth(self, path: str) -> str | None | bool:
        """Validate Bearer token for /v1/* paths.

        Args:
            path: The request path.

        Returns:
            - ``str``: authenticated agent_id
            - ``None``: authenticated supertoken (any agent)
            - ``False``: auth failed (401 already sent)
            - ``True``: auth not required (public path or no token_store)
        """
        # Public paths skip auth
        if path in _PUBLIC_PATHS:
            return True

        # No token store = auth disabled
        if self.token_store is None:
            return True

        # No tokens configured = auth disabled (backward compat)
        if not self.token_store.has_tokens():
            return True

        # Extract Bearer token
        auth_header = self.headers.get("Authorization", "")
        if not auth_header:
            self._error(401, "Unauthorized", "Missing Authorization header")
            return False

        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            self._error(
                401, "Unauthorized", "Invalid Authorization format, expected 'Bearer <token>'"
            )
            return False

        result = self.token_store.validate(parts[1])
        if result is False:
            self._error(401, "Unauthorized", "Invalid or expired token")
            return False

        # result is str (agent_id) or None (supertoken)
        return result

    # ── Routing ───────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = urllib.parse.parse_qs(parsed.query)

        # Auth check for /v1/* paths
        if path.startswith("/v1") or path == "/health":
            auth_result = self._check_auth(path)
            if auth_result is False:
                return
        else:
            auth_result = True

        routes: dict[str, Any] = {
            "/v1/query": lambda: self._handle_query(qs, auth_result),
            "/v1/channels": lambda: self._handle_channels(),
            "/v1/subscribe": lambda: self._handle_subscribe(qs, auth_result),
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

        # Auth check
        auth_result = self._check_auth(path)
        if auth_result is False:
            return

        body = self._read_body()
        if body is None:
            return  # error already sent

        if path == "/v1/publish":
            self._handle_publish(body, auth_result)
        else:
            self._error(404, "Not Found", f"No route: {path}")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ── Handlers ──────────────────────────────────────────────────

    def _handle_publish(self, data: dict, auth_result: Any) -> None:
        required = ("channel", "sender", "msg_type", "payload")
        missing = [f for f in required if f not in data]
        if missing:
            self._error(400, "Bad Request", f"Missing: {', '.join(missing)}")
            return

        # Enforce sender match: token's agent_id must match body sender.
        # Supertokens (auth_result=None) and no-auth (auth_result=True) skip.
        if isinstance(auth_result, str) and data["sender"] != auth_result:
            self._error(
                403,
                "Forbidden",
                f"Token is bound to agent '{auth_result}', cannot publish as '{data['sender']}'",
            )
            return

        msg_id = self.bus.publish(
            channel=data["channel"],
            sender=data["sender"],
            msg_type=data["msg_type"],
            payload=data["payload"],
            metadata=data.get("metadata"),
        )
        self._json({"message_id": msg_id})

    def _handle_query(self, qs: dict[str, list[str]], auth_result: Any) -> None:
        channel = self._qs_first(qs, "channel")
        if not channel:
            self._error(400, "Bad Request", "Query param 'channel' required")
            return

        after = self._qs_first(qs, "after")
        try:
            limit = min(int(self._qs_first(qs, "limit") or "100"), self.max_query_limit)
        except ValueError:
            self._error(400, "Bad Request", "Query param 'limit' must be an integer")
            return

        msgs = self.bus.poll(channel, after=after, limit=limit)

        # Read isolation: scoped tokens only see messages where they are
        # the sender or recipient (DM channels containing their agent_id).
        if isinstance(auth_result, str):
            msgs = [m for m in msgs if _agent_involved(auth_result, m.channel, m.sender)]

        self._json(
            {
                "messages": [self._msg_to_dict(m) for m in msgs],
                "count": len(msgs),
            }
        )

    def _handle_channels(self) -> None:
        self._json({"channels": self.bus.channels()})

    def _handle_auth_check(self) -> None:
        """Return whether auth is required and token store status."""
        has_tokens = bool(self.token_store and self.token_store.has_tokens())
        self._json(
            {
                "require_auth": self.bus.require_auth,
                "token_auth_enabled": has_tokens,
            }
        )

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

    def _handle_subscribe(self, qs: dict[str, list[str]], auth_result: Any) -> None:
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

        # Read isolation: scoped tokens can only subscribe to channels
        # that contain their agent_id (e.g., DM channels).
        # Supertokens and no-auth skip this check.
        if isinstance(auth_result, str):
            forbidden = [
                ch
                for ch in channels
                if not ch.startswith("_system:") and auth_result not in ch.split(":")
            ]
            if forbidden:
                self._error(
                    403,
                    "Forbidden",
                    f"Token for '{auth_result}' cannot subscribe to: {', '.join(forbidden)}",
                )
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
                # Read isolation: scoped tokens skip messages not involving them
                if isinstance(auth_result, str) and not _agent_involved(
                    auth_result, msg.channel, msg.sender
                ):
                    return
                try:
                    client.q.put_nowait({"channel": ch, "message": self._msg_to_dict(msg)})
                except queue.Full:
                    client.dropped.increment()

            return _cb

        for ch in channels:
            sub_id = self.bus.subscribe(ch, _make_callback(ch))
            client.sub_ids.append(sub_id)

        with self.sse_lock:
            self.sse_clients.append(client)

        self._sse_stream_loop(client)

    # ── SSE Stream ────────────────────────────────────────────────

    def _sse_stream_loop(self, client: _SseClient) -> None:
        """Stream SSE events until client disconnects or shutdown."""
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()

            while True:
                try:
                    event = client.q.get(timeout=15)
                except queue.Empty:
                    if not self._sse_keepalive(client):
                        break
                    continue

                if event is None:
                    break  # shutdown signal

                data = json.dumps(event, ensure_ascii=False)
                try:
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
        finally:
            for sub_id in client.sub_ids:
                self.bus.unsubscribe(sub_id)
            with self.sse_lock, contextlib.suppress(ValueError):
                self.sse_clients.remove(client)

    def _sse_keepalive(self, client: _SseClient) -> bool:
        """Send keepalive comment or drop notification. Returns False on disconnect."""
        try:
            dropped = client.dropped.drain()
            if dropped > 0:
                self.wfile.write(f": {dropped} event(s) dropped (slow consumer)\n\n".encode())
            else:
                self.wfile.write(b": keepalive\n\n")
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    # ── Helpers ───────────────────────────────────────────────────

    def _read_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._error(400, "Bad Request", "Empty body")
            return None
        if length > self.max_body_bytes:
            self._error(413, "Payload Too Large", f"Body exceeds {self.max_body_bytes} bytes")
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
        self.send_header("Access-Control-Allow-Origin", self.cors_origin)
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
        cors_origin: Access-Control-Allow-Origin value. Default "*".
        max_body_bytes: Maximum request body size in bytes. Default 1MB.
        max_query_limit: Maximum query limit parameter. Default 10000.
        token_store: Optional TokenStore for API authentication. None disables auth.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8741,
        cors_origin: str = "*",
        max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES,
        max_query_limit: int = _DEFAULT_MAX_QUERY_LIMIT,
        token_store: TokenStore | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._cors_origin = cors_origin
        self._max_body_bytes = max_body_bytes
        self._max_query_limit = max_query_limit
        self._token_store = token_store
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
                "token_store": self._token_store,
                "sse_clients": self._sse_clients,
                "sse_lock": self._sse_lock,
                "cors_origin": self._cors_origin,
                "max_body_bytes": self._max_body_bytes,
                "max_query_limit": self._max_query_limit,
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
            host = addr[0] if isinstance(addr[0], str) else addr[0].decode()
            return (host, int(addr[1]))
        return (self._host, self._port)

    def __repr__(self) -> str:
        addr = f"{self._host}:{self._port}"
        status = "attached" if self._bus else "detached"
        auth = "auth" if self._token_store else "no-auth"
        return f"HttpFrontend({addr}, {status}, {auth})"
