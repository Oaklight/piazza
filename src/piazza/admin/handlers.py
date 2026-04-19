"""HTTP request handlers for admin panel.

Implements REST API endpoints for monitoring and interacting
with the piazza message bus.
"""

import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any, ClassVar

from .static import ADMIN_HTML

if TYPE_CHECKING:
    from piazza.bus import Bus

    from .auth import TokenAuth


class AdminRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for piazza admin panel.

    Implements REST API endpoints for:
    - Dashboard statistics and throughput
    - Channel listing and details
    - Message browsing and publishing
    - Subscription visibility

    Class Attributes:
        bus: The Bus instance to monitor.
        auth: Optional TokenAuth instance for authentication.
        serve_ui: Whether to serve the admin UI at root path.
    """

    bus: ClassVar["Bus"]
    auth: ClassVar["TokenAuth | None"]
    serve_ui: ClassVar[bool]

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP logging."""

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.auth and not self.auth.require_auth(self):
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            self._handle_root()
        elif path == "/api/stats":
            self._handle_get_stats()
        elif path == "/api/stats/throughput":
            self._handle_get_throughput()
        elif path == "/api/channels":
            self._handle_get_channels()
        elif path.startswith("/api/channels/"):
            name = urllib.parse.unquote(path[len("/api/channels/") :])
            self._handle_get_channel(name)
        elif path == "/api/messages":
            self._handle_get_messages(query)
        elif path == "/api/subscriptions":
            self._handle_get_subscriptions()
        else:
            self._send_not_found()

    def do_POST(self) -> None:
        """Handle POST requests."""
        if self.auth and not self.auth.require_auth(self):
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if path == "/api/messages":
            self._handle_publish_message(body)
        else:
            self._send_not_found()

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    # ============== Response Helpers ==============

    def _send_cors_headers(self) -> None:
        """Send CORS headers."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_json_response(self, data: Any, status: int = 200) -> None:
        """Send a JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._send_cors_headers()
        self.end_headers()
        response = json.dumps(data, default=self._json_serializer)
        self.wfile.write(response.encode("utf-8"))

    def _send_error_response(self, status: int, error: str, message: str) -> None:
        """Send an error response."""
        self._send_json_response({"error": error, "message": message}, status)

    def _send_not_found(self) -> None:
        """Send a 404 Not Found response."""
        self._send_error_response(404, "Not Found", f"Path not found: {self.path}")

    @staticmethod
    def _json_serializer(obj: Any) -> Any:
        """Custom JSON serializer for datetime objects."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    # ============== Route Handlers ==============

    def _handle_root(self) -> None:
        """Handle root path - serve UI or API index."""
        if self.serve_ui:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(ADMIN_HTML.encode("utf-8"))
        else:
            self._send_json_response(
                {
                    "name": "Piazza Admin API",
                    "version": "1.0.0",
                    "endpoints": [
                        "GET /api/stats",
                        "GET /api/stats/throughput",
                        "GET /api/channels",
                        "GET /api/channels/{name}",
                        "GET /api/messages?channel=...",
                        "POST /api/messages",
                        "GET /api/subscriptions",
                    ],
                }
            )

    def _handle_get_stats(self) -> None:
        """Handle GET /api/stats - dashboard aggregates."""
        stats = self.bus.backend.get_stats()

        # Add subscription count from bus internals
        total_subs = sum(len(subs) for subs in self.bus._subs.values())
        stats["active_subscriptions"] = total_subs

        self._send_json_response(stats)

    def _handle_get_throughput(self) -> None:
        """Handle GET /api/stats/throughput - per-second message counts."""
        timestamps = self.bus.backend.query_recent_timestamps(60)

        now = datetime.now(timezone.utc)
        buckets = []
        for i in range(60):
            bucket_time = now - timedelta(seconds=59 - i)
            bucket_iso = bucket_time.replace(microsecond=0).isoformat()
            buckets.append({"timestamp": bucket_iso, "count": 0})

        # Bucket timestamps by second
        for ts_str in timestamps:
            try:
                ts = datetime.fromisoformat(ts_str)
                delta = (now - ts).total_seconds()
                if 0 <= delta < 60:
                    idx = 59 - int(delta)
                    if 0 <= idx < 60:
                        buckets[idx]["count"] += 1
            except (ValueError, IndexError):
                continue

        self._send_json_response({"window_seconds": 60, "buckets": buckets})

    def _handle_get_channels(self) -> None:
        """Handle GET /api/channels - list all channels with stats."""
        stats = self.bus.backend.get_stats()
        channels = []
        for bd in stats.get("channel_breakdown", []):
            ch_name = bd["channel"]
            sub_count = len(self.bus._subs.get(ch_name, {}))
            channels.append(
                {
                    "name": ch_name,
                    "message_count": bd["message_count"],
                    "last_message_time": bd["last_message_time"],
                    "sender_count": bd["sender_count"],
                    "subscription_count": sub_count,
                }
            )
        self._send_json_response({"channels": channels})

    def _handle_get_channel(self, name: str) -> None:
        """Handle GET /api/channels/{name} - channel detail."""
        count = self.bus.backend.count_messages(name)
        if count == 0:
            self._send_error_response(404, "Not Found", f"Channel not found: {name}")
            return

        # Get senders and msg_type distribution via query_all
        all_msgs = self.bus.backend.query_all(channel=name, limit=10000)
        senders = sorted({m.sender for m in all_msgs})
        type_counts: dict[str, int] = {}
        last_time = ""
        for m in all_msgs:
            type_counts[m.msg_type] = type_counts.get(m.msg_type, 0) + 1
            if m.timestamp > last_time:
                last_time = m.timestamp

        msg_types = [
            {"msg_type": t, "count": c}
            for t, c in sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
        ]
        sub_count = len(self.bus._subs.get(name, {}))

        self._send_json_response(
            {
                "name": name,
                "message_count": count,
                "last_message_time": last_time,
                "senders": senders,
                "msg_types": msg_types,
                "subscription_count": sub_count,
            }
        )

    def _handle_get_messages(self, query: dict[str, list[str]]) -> None:
        """Handle GET /api/messages - query messages with filters."""
        channel_list = query.get("channel", [])
        if not channel_list or not channel_list[0]:
            self._send_error_response(400, "Bad Request", "Query parameter 'channel' is required")
            return

        channel = channel_list[0]
        after = query.get("after", [None])[0]
        limit = int(query.get("limit", ["100"])[0])
        sender = query.get("sender", [None])[0]
        msg_type = query.get("msg_type", [None])[0]

        if sender or msg_type:
            messages = self.bus.backend.query_all(
                after=after,
                limit=limit,
                channel=channel,
                sender=sender,
                msg_type=msg_type,
            )
        else:
            messages = self.bus.poll(channel, after=after, limit=limit)

        msgs_data = [
            {
                "id": m.id,
                "channel": m.channel,
                "sender": m.sender,
                "msg_type": m.msg_type,
                "payload": m.payload,
                "timestamp": m.timestamp,
                "metadata": m.metadata,
            }
            for m in messages
        ]

        self._send_json_response(
            {
                "messages": msgs_data,
                "count": len(msgs_data),
                "has_more": len(msgs_data) == limit,
            }
        )

    def _handle_publish_message(self, body: bytes) -> None:
        """Handle POST /api/messages - publish a new message."""
        if not body:
            self._send_error_response(400, "Bad Request", "Request body is required")
            return

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            self._send_error_response(400, "Bad Request", f"Invalid JSON: {e}")
            return

        required = ["channel", "sender", "msg_type", "payload"]
        missing = [f for f in required if f not in data]
        if missing:
            self._send_error_response(
                400, "Bad Request", f"Missing required fields: {', '.join(missing)}"
            )
            return

        msg_id = self.bus.publish(
            channel=data["channel"],
            sender=data["sender"],
            msg_type=data["msg_type"],
            payload=data["payload"],
            metadata=data.get("metadata"),
        )

        self._send_json_response({"success": True, "message_id": msg_id})

    def _handle_get_subscriptions(self) -> None:
        """Handle GET /api/subscriptions - list in-process subscriptions."""
        channels = []
        total = 0
        for channel, subs in sorted(self.bus._subs.items()):
            if subs:
                sub_ids = list(subs.keys())
                channels.append(
                    {
                        "channel": channel,
                        "subscription_ids": sub_ids,
                        "count": len(sub_ids),
                    }
                )
                total += len(sub_ids)

        self._send_json_response({"total": total, "channels": channels})
