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

        id1 = transport.publish("test-ch", "a", "chat", "first")
        id2 = transport.publish("test-ch", "a", "chat", "second")
        id3 = transport.publish("test-ch", "a", "chat", "third")

        msgs = transport.query("test-ch", after=id1)
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


# ── Channel Ownership Auth Tests ─────────────────────────────────


@pytest.fixture()
def auth_server(tmp_path):
    """Start a PiazzaServer with token auth enabled, yield (url, token_store)."""
    from piazza.token_store import TokenStore

    db_path = str(tmp_path / "auth_test.db")
    token_store = TokenStore(db_path)

    bus = Bus(backend=MemoryBackend())
    frontend = HttpFrontend(host="127.0.0.1", port=0, token_store=token_store)
    server = PiazzaServer(bus)
    server.add_frontend(frontend)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)

    host, port = frontend.address
    url = f"http://{host}:{port}"

    yield url, token_store

    server.shutdown()


class TestChannelOwnership:
    """Private channel ownership enforcement (notebook:X, memory:X)."""

    def test_cross_agent_notebook_write_blocked(self, auth_server) -> None:
        """Elena cannot write to notebook:milo — must get 403."""
        from piazza._vendor.httpclient import Client as HttpClient

        url, store = auth_server

        milo_token = store.create_token("milo", "Milo's token")["token"]
        elena_token = store.create_token("elena", "Elena's token")["token"]

        # Milo can write to his own notebook
        milo_transport = HttpTransport(url, agent_id="milo", token=milo_token)
        msg_id = milo_transport.publish("notebook:milo", "milo", "note", "my private note")
        assert msg_id
        milo_transport.close()

        # Elena tries to write to Milo's notebook — should get 403
        http = HttpClient(
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {elena_token}",
            }
        )
        resp = http.post(
            f"{url}/v1/publish",
            json={
                "channel": "notebook:milo",
                "sender": "elena",
                "msg_type": "note",
                "payload": "snooping",
            },
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"] == "Forbidden"
        assert "notebook:milo" in body["message"]
        http.close()

    def test_cross_agent_memory_write_blocked(self, auth_server) -> None:
        """An agent cannot write to another agent's memory channel."""
        from piazza._vendor.httpclient import Client as HttpClient

        url, store = auth_server

        alice_token = store.create_token("alice", "Alice's token")["token"]
        bob_token = store.create_token("bob", "Bob's token")["token"]

        # Alice can write to her own memory
        alice_transport = HttpTransport(url, agent_id="alice", token=alice_token)
        msg_id = alice_transport.publish("memory:alice", "alice", "memo", "remember this")
        assert msg_id
        alice_transport.close()

        # Bob tries to write to Alice's memory — should get 403
        http = HttpClient(
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {bob_token}",
            }
        )
        resp = http.post(
            f"{url}/v1/publish",
            json={
                "channel": "memory:alice",
                "sender": "bob",
                "msg_type": "memo",
                "payload": "injecting",
            },
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"] == "Forbidden"
        assert "memory:alice" in body["message"]
        http.close()

    def test_own_notebook_write_allowed(self, auth_server) -> None:
        """An agent can write to its own notebook channel."""
        url, store = auth_server
        token = store.create_token("agent-x", "X's token")["token"]

        transport = HttpTransport(url, agent_id="agent-x", token=token)
        msg_id = transport.publish("notebook:agent-x", "agent-x", "note", "my note")
        assert msg_id

        msgs = transport.query("notebook:agent-x")
        assert len(msgs) == 1
        assert msgs[0].payload == "my note"
        transport.close()

    def test_supertoken_bypasses_ownership(self, auth_server) -> None:
        """Supertokens can write to any private channel."""
        url, store = auth_server
        super_token = store.create_token(agent_id=None, label="admin")["token"]

        transport = HttpTransport(url, agent_id="admin", token=super_token)
        msg_id = transport.publish("notebook:milo", "admin", "note", "admin override")
        assert msg_id
        transport.close()


class TestSystemChannelAccess:
    """_system:* channel write restrictions."""

    def test_arbitrary_write_to_system_agents_blocked(self, auth_server) -> None:
        """Regular agents cannot write arbitrary messages to _system:agents."""
        from piazza._vendor.httpclient import Client as HttpClient

        url, store = auth_server
        token = store.create_token("agent-a", "A's token")["token"]

        http = HttpClient(
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            }
        )
        resp = http.post(
            f"{url}/v1/publish",
            json={
                "channel": "_system:agents",
                "sender": "agent-a",
                "msg_type": "chat",  # not "presence"
                "payload": "injected",
            },
        )
        assert resp.status_code == 403
        assert "reserved for system use" in resp.json()["message"]
        http.close()

    def test_presence_write_to_system_agents_allowed(self, auth_server) -> None:
        """Agents can write presence to _system:agents (used by SDK _announce)."""
        url, store = auth_server
        token = store.create_token("agent-b", "B's token")["token"]

        transport = HttpTransport(url, agent_id="agent-b", token=token)
        msg_id = transport.publish("_system:agents", "agent-b", "presence", '{"status": "online"}')
        assert msg_id
        transport.close()

    def test_write_to_unknown_system_channel_blocked(self, auth_server) -> None:
        """Agents cannot write to arbitrary _system:* channels."""
        from piazza._vendor.httpclient import Client as HttpClient

        url, store = auth_server
        token = store.create_token("agent-c", "C's token")["token"]

        http = HttpClient(
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            }
        )
        resp = http.post(
            f"{url}/v1/publish",
            json={
                "channel": "_system:evil",
                "sender": "agent-c",
                "msg_type": "presence",
                "payload": "fake",
            },
        )
        assert resp.status_code == 403
        http.close()

    def test_supertoken_bypasses_system_restriction(self, auth_server) -> None:
        """Supertokens can write anything to _system:* channels."""
        url, store = auth_server
        super_token = store.create_token(agent_id=None, label="admin")["token"]

        transport = HttpTransport(url, agent_id="admin", token=super_token)
        msg_id = transport.publish("_system:agents", "admin", "chat", "admin can do anything")
        assert msg_id
        transport.close()
