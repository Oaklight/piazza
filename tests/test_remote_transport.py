"""Tests for HTTP remote transport — HttpFrontend + HttpTransport.

Verifies:
- PiazzaServer + HttpFrontend serves the Bus over HTTP
- HttpTransport implements Transport protocol correctly
- PiazzaClient("http://...") resolves to HttpTransport
- DM round-trip between two remote clients
- SSE push notifications
- Channel listing and query
"""

from __future__ import annotations

import threading
import time

import pytest

from piazza import Bus, MemoryBackend, PiazzaClient, PiazzaServer
from piazza.frontends import HttpFrontend
from piazza.transport_http import HttpTransport


@pytest.fixture()
def server_url():
    """Start a PiazzaServer with HttpFrontend on a random port, yield URL."""
    bus = Bus(backend=MemoryBackend())
    frontend = HttpFrontend(host="127.0.0.1", port=0)  # OS-assigned port
    server = PiazzaServer(bus)
    server.add_frontend(frontend)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Wait for server to start
    time.sleep(0.3)
    host, port = frontend.address
    url = f"http://{host}:{port}"

    yield url

    server.shutdown()


@pytest.fixture()
def server_url_with_auth():
    """Start a PiazzaServer with auth enabled."""
    bus = Bus(backend=MemoryBackend(), require_auth=True)
    frontend = HttpFrontend(host="127.0.0.1", port=0)
    server = PiazzaServer(bus)
    server.add_frontend(frontend)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)
    host, port = frontend.address
    url = f"http://{host}:{port}"

    yield url

    server.shutdown()


# ── Transport Protocol Tests ─────────────────────────────────────


class TestHttpTransport:
    """Test HttpTransport implements Transport protocol."""

    def test_publish_and_query(self, server_url: str) -> None:
        transport = HttpTransport(server_url, agent_id="test-agent")
        msg_id = transport.publish(
            channel="test-ch",
            sender="test-agent",
            msg_type="chat",
            payload="hello remote",
        )
        assert msg_id

        msgs = transport.query("test-ch")
        assert len(msgs) == 1
        assert msgs[0].payload == "hello remote"
        assert msgs[0].sender == "test-agent"
        assert msgs[0].id == msg_id

        transport.close()

    def test_query_with_cursor(self, server_url: str) -> None:
        transport = HttpTransport(server_url, agent_id="test-agent")

        id1 = transport.publish("ch", "a", "chat", "first")
        id2 = transport.publish("ch", "a", "chat", "second")
        id3 = transport.publish("ch", "a", "chat", "third")

        msgs = transport.query("ch", after=id1)
        assert len(msgs) == 2
        assert msgs[0].id == id2
        assert msgs[1].id == id3

        transport.close()

    def test_list_channels(self, server_url: str) -> None:
        transport = HttpTransport(server_url, agent_id="test-agent")

        transport.publish("alpha", "a", "chat", "x")
        transport.publish("beta", "a", "chat", "y")

        channels = transport.list_channels()
        assert "alpha" in channels
        assert "beta" in channels

        transport.close()

    def test_require_auth(self, server_url: str) -> None:
        transport = HttpTransport(server_url, agent_id="test-agent")
        assert transport.require_auth is False
        transport.close()


# ── PiazzaClient over HTTP Tests ─────────────────────────────────


class TestClientOverHttp:
    """Test PiazzaClient with http:// connection string."""

    def test_client_connects_via_url(self, server_url: str) -> None:
        """PiazzaClient("http://...") should use HttpTransport."""
        client = PiazzaClient(server_url, "remote-agent")
        assert "HttpTransport" in repr(client._transport)
        client.close()

    def test_dm_round_trip(self, server_url: str) -> None:
        """Two clients connected to the same server can DM each other."""
        alice = PiazzaClient(server_url, "alice")
        bob = PiazzaClient(server_url, "bob")

        alice.dm_send("bob", "hey bob!")
        msgs = bob.dm_read("alice", limit=10)
        assert len(msgs) == 1
        assert msgs[0].payload == "hey bob!"
        assert msgs[0].sender == "alice"

        bob.dm_send("alice", "hey alice!")
        msgs = alice.dm_read("bob", limit=10)
        assert len(msgs) == 2  # both messages in same DM channel
        assert msgs[1].payload == "hey alice!"

        alice.close()
        bob.close()

    def test_broadcast(self, server_url: str) -> None:
        """Broadcast send + read over HTTP."""
        sender = PiazzaClient(server_url, "broadcaster")
        reader = PiazzaClient(server_url, "listener")

        sender.channel_send("broadcast:news", "breaking news!", msg_type="announcement")

        msgs = reader.channel_read("broadcast:news", limit=10)
        assert len(msgs) >= 1
        assert any(m.payload == "breaking news!" for m in msgs)

        sender.close()
        reader.close()

    def test_channel_poll_with_cursor(self, server_url: str) -> None:
        """channel_poll advances cursor so repeated polls don't re-read."""
        client = PiazzaClient(server_url, "poller")

        client.channel_send("updates", "msg-1")
        client.channel_send("updates", "msg-2")

        batch1 = client.channel_poll("updates")
        assert len(batch1) >= 2

        client.channel_send("updates", "msg-3")
        batch2 = client.channel_poll("updates")
        assert len(batch2) == 1
        assert batch2[0].payload == "msg-3"

        client.close()

    def test_note_write_read(self, server_url: str) -> None:
        """Note (private scratchpad) via HTTP."""
        client = PiazzaClient(server_url, "noter")
        client.note_write("remember this")
        notes = client.note_read()
        assert len(notes) >= 1
        assert any(n.payload == "remember this" for n in notes)
        client.close()


# ── SSE Push Tests ────────────────────────────────────────────────


class TestSsePush:
    """Test SSE real-time notifications."""

    def test_sse_receives_new_messages(self, server_url: str) -> None:
        """Subscribe via SSE and receive push when a message is published."""
        received: list = []
        event = threading.Event()

        transport = HttpTransport(server_url, agent_id="sse-listener")

        def on_msg(msg):
            received.append(msg)
            event.set()

        transport.subscribe("sse-test", on_msg)
        time.sleep(0.5)  # let SSE connection establish

        # Publish from a different transport
        sender = HttpTransport(server_url, agent_id="sse-sender")
        sender.publish("sse-test", "sse-sender", "chat", "live push!")

        assert event.wait(timeout=5), "SSE event not received within 5s"
        assert len(received) == 1
        assert received[0].payload == "live push!"
        assert received[0].sender == "sse-sender"

        sender.close()
        transport.close()

    def test_sse_multiple_channels(self, server_url: str) -> None:
        """SSE can subscribe to multiple channels."""
        received_a: list = []
        received_b: list = []
        done = threading.Event()

        transport = HttpTransport(server_url, agent_id="multi-sub")
        transport.subscribe("chan-a", lambda m: received_a.append(m))

        def _on_b(m):
            received_b.append(m)
            done.set()

        transport.subscribe("chan-b", _on_b)
        time.sleep(0.5)

        sender = HttpTransport(server_url, agent_id="multi-pub")
        sender.publish("chan-a", "multi-pub", "chat", "to A")
        sender.publish("chan-b", "multi-pub", "chat", "to B")

        assert done.wait(timeout=5)
        time.sleep(0.2)  # let chan-a event arrive too

        assert len(received_a) == 1
        assert received_a[0].payload == "to A"
        assert len(received_b) == 1
        assert received_b[0].payload == "to B"

        sender.close()
        transport.close()

    def test_unsubscribe_stops_delivery(self, server_url: str) -> None:
        """After unsubscribe, no more events are delivered."""
        received: list = []

        transport = HttpTransport(server_url, agent_id="unsub-test")
        sub_id = transport.subscribe("unsub-ch", lambda m: received.append(m))
        time.sleep(0.5)

        sender = HttpTransport(server_url, agent_id="unsub-sender")
        sender.publish("unsub-ch", "unsub-sender", "chat", "before unsub")
        time.sleep(0.5)
        assert len(received) == 1

        transport.unsubscribe(sub_id)
        time.sleep(0.3)

        sender.publish("unsub-ch", "unsub-sender", "chat", "after unsub")
        time.sleep(0.5)
        # Should not receive the second message (callback removed)
        # Note: the SSE stream may still be connected, but dispatch won't find callback
        assert len(received) == 1

        sender.close()
        transport.close()


# ── Server Lifecycle Tests ────────────────────────────────────────


class TestServerLifecycle:
    """Test PiazzaServer lifecycle."""

    def test_health_endpoint(self, server_url: str) -> None:
        import json
        import urllib.request

        resp = urllib.request.urlopen(f"{server_url}/health", timeout=3)
        data = json.loads(resp.read())
        assert data["status"] == "ok"

    def test_no_frontend_raises(self) -> None:
        bus = Bus(backend=MemoryBackend())
        server = PiazzaServer(bus)
        with pytest.raises(RuntimeError, match="No frontends"):
            server.serve_forever()

    def test_frontend_not_attached_raises(self) -> None:
        frontend = HttpFrontend()
        with pytest.raises(RuntimeError, match="Must call attach"):
            frontend.serve_forever()
