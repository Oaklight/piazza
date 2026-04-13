"""Tests for piazza message bus and modular components."""

import json
import sqlite3

import pytest

from piazza import (
    Bus,
    JSONSerializer,
    MemoryStorage,
    Message,
    SQLiteBus,
    SQLiteStorage,
)

# ──────────────────────────────────────────────
# Message dataclass
# ──────────────────────────────────────────────


class TestMessage:
    """Tests for Message dataclass."""

    def test_create_message(self):
        msg = Message(
            id="test-id",
            channel="test-ch",
            sender="agent-a",
            msg_type="text",
            payload="hello",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        assert msg.id == "test-id"
        assert msg.channel == "test-ch"
        assert msg.sender == "agent-a"
        assert msg.msg_type == "text"
        assert msg.payload == "hello"
        assert msg.metadata is None

    def test_message_is_frozen(self):
        msg = Message(
            id="test-id",
            channel="ch",
            sender="a",
            msg_type="text",
            payload="x",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        with pytest.raises(AttributeError):
            msg.id = "new-id"

    def test_payload_json(self):
        data = {"key": "value", "count": 42}
        msg = Message(
            id="test-id",
            channel="ch",
            sender="a",
            msg_type="json",
            payload=json.dumps(data),
            timestamp="2026-01-01T00:00:00+00:00",
        )
        assert msg.payload_json() == data

    def test_payload_json_invalid(self):
        msg = Message(
            id="test-id",
            channel="ch",
            sender="a",
            msg_type="text",
            payload="not json",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        with pytest.raises(json.JSONDecodeError):
            msg.payload_json()

    def test_metadata(self):
        meta = {"priority": "high", "tags": ["urgent"]}
        msg = Message(
            id="test-id",
            channel="ch",
            sender="a",
            msg_type="text",
            payload="x",
            timestamp="2026-01-01T00:00:00+00:00",
            metadata=meta,
        )
        assert msg.metadata == meta


# ──────────────────────────────────────────────
# JSONSerializer
# ──────────────────────────────────────────────


class TestJSONSerializer:
    """Tests for JSONSerializer."""

    def test_encode_decode_roundtrip(self):
        ser = JSONSerializer()
        data = {"key": "value", "nested": {"a": 1}}
        encoded = ser.encode(data)
        assert isinstance(encoded, str)
        assert ser.decode(encoded) == data

    def test_encode_unicode(self):
        ser = JSONSerializer()
        data = {"name": "piazza 广场"}
        encoded = ser.encode(data)
        assert "piazza 广场" in encoded  # ensure_ascii=False


# ──────────────────────────────────────────────
# MemoryStorage
# ──────────────────────────────────────────────


class TestMemoryStorage:
    """Tests for MemoryStorage backend."""

    def _make_msg(self, channel="ch", msg_id="id-1", payload="x"):
        return Message(
            id=msg_id,
            channel=channel,
            sender="a",
            msg_type="text",
            payload=payload,
            timestamp="2026-01-01T00:00:00+00:00",
        )

    def test_store_and_query(self):
        storage = MemoryStorage()
        msg = self._make_msg()
        storage.store(msg)
        result = storage.query("ch")
        assert len(result) == 1
        assert result[0].payload == "x"

    def test_query_empty(self):
        storage = MemoryStorage()
        assert storage.query("nonexistent") == []

    def test_query_with_after(self):
        storage = MemoryStorage()
        storage.store(self._make_msg(msg_id="aaa"))
        storage.store(self._make_msg(msg_id="bbb"))
        storage.store(self._make_msg(msg_id="ccc"))
        result = storage.query("ch", after="aaa")
        assert len(result) == 2
        assert result[0].id == "bbb"

    def test_query_with_limit(self):
        storage = MemoryStorage()
        for i in range(5):
            storage.store(self._make_msg(msg_id=f"id-{i:03d}"))
        result = storage.query("ch", limit=2)
        assert len(result) == 2

    def test_list_channels(self):
        storage = MemoryStorage()
        storage.store(self._make_msg(channel="beta"))
        storage.store(self._make_msg(channel="alpha"))
        assert storage.list_channels() == ["alpha", "beta"]

    def test_close_clears(self):
        storage = MemoryStorage()
        storage.store(self._make_msg())
        storage.close()
        assert storage.query("ch") == []

    def test_repr(self):
        assert repr(MemoryStorage()) == "MemoryStorage()"


# ──────────────────────────────────────────────
# SQLiteStorage
# ──────────────────────────────────────────────


class TestSQLiteStorage:
    """Tests for SQLiteStorage backend."""

    def _make_msg(self, channel="ch", msg_id="id-1", payload="x", metadata=None):
        return Message(
            id=msg_id,
            channel=channel,
            sender="a",
            msg_type="text",
            payload=payload,
            timestamp="2026-01-01T00:00:00+00:00",
            metadata=metadata,
        )

    def test_store_and_query(self):
        storage = SQLiteStorage()
        storage.store(self._make_msg())
        result = storage.query("ch")
        assert len(result) == 1
        assert result[0].payload == "x"
        storage.close()

    def test_metadata_roundtrip(self):
        storage = SQLiteStorage()
        meta = {"tags": ["a", "b"]}
        storage.store(self._make_msg(metadata=meta))
        result = storage.query("ch")
        assert result[0].metadata == meta
        storage.close()

    def test_persistence(self, tmp_path):
        db = tmp_path / "test.db"
        storage = SQLiteStorage(db)
        storage.store(self._make_msg())
        storage.close()

        storage2 = SQLiteStorage(db)
        result = storage2.query("ch")
        assert len(result) == 1
        storage2.close()

    def test_repr(self):
        storage = SQLiteStorage()
        assert repr(storage) == "SQLiteStorage(':memory:')"
        storage.close()


# ──────────────────────────────────────────────
# Bus (composable)
# ──────────────────────────────────────────────


class TestBusWithMemoryStorage:
    """Tests for Bus with MemoryStorage backend."""

    def test_publish_and_poll(self):
        with Bus(storage=MemoryStorage()) as bus:
            bus.publish("ch", "agent-a", "text", "msg1")
            bus.publish("ch", "agent-b", "text", "msg2")
            msgs = bus.poll("ch")
            assert len(msgs) == 2
            assert msgs[0].payload == "msg1"
            assert msgs[1].payload == "msg2"

    def test_subscribe(self):
        received = []
        with Bus(storage=MemoryStorage()) as bus:
            bus.subscribe("ch", lambda msg: received.append(msg))
            bus.publish("ch", "a", "text", "hello")
            assert len(received) == 1
            assert received[0].payload == "hello"

    def test_storage_property(self):
        mem = MemoryStorage()
        bus = Bus(storage=mem)
        assert bus.storage is mem
        bus.close()

    def test_serializer_property(self):
        bus = Bus(storage=MemoryStorage())
        assert isinstance(bus.serializer, JSONSerializer)
        bus.close()

    def test_repr(self):
        bus = Bus(storage=MemoryStorage())
        assert repr(bus) == "Bus(storage=MemoryStorage())"
        bus.close()


# ──────────────────────────────────────────────
# SQLiteBus (convenience subclass)
# ──────────────────────────────────────────────


class TestSQLiteBusInMemory:
    """Tests for SQLiteBus using in-memory database."""

    def test_publish_returns_id(self):
        with SQLiteBus() as bus:
            msg_id = bus.publish("ch", "agent-a", "text", "hello")
            assert isinstance(msg_id, str)
            assert len(msg_id) > 0

    def test_publish_and_poll(self):
        with SQLiteBus() as bus:
            bus.publish("ch", "agent-a", "text", "msg1")
            bus.publish("ch", "agent-b", "text", "msg2")
            msgs = bus.poll("ch")
            assert len(msgs) == 2
            assert msgs[0].payload == "msg1"
            assert msgs[1].payload == "msg2"
            assert msgs[0].sender == "agent-a"
            assert msgs[1].sender == "agent-b"

    def test_poll_empty_channel(self):
        with SQLiteBus() as bus:
            assert bus.poll("nonexistent") == []

    def test_poll_with_cursor(self):
        with SQLiteBus() as bus:
            id1 = bus.publish("ch", "a", "text", "msg1")
            bus.publish("ch", "a", "text", "msg2")
            bus.publish("ch", "a", "text", "msg3")
            msgs = bus.poll("ch", after=id1)
            assert len(msgs) == 2
            assert msgs[0].payload == "msg2"
            assert msgs[1].payload == "msg3"

    def test_poll_with_limit(self):
        with SQLiteBus() as bus:
            for i in range(10):
                bus.publish("ch", "a", "text", f"msg{i}")
            msgs = bus.poll("ch", limit=3)
            assert len(msgs) == 3

    def test_poll_channel_isolation(self):
        with SQLiteBus() as bus:
            bus.publish("ch1", "a", "text", "in-ch1")
            bus.publish("ch2", "a", "text", "in-ch2")
            assert bus.poll("ch1")[0].payload == "in-ch1"
            assert bus.poll("ch2")[0].payload == "in-ch2"

    def test_poll_chronological_order(self):
        with SQLiteBus() as bus:
            for i in range(5):
                bus.publish("ch", "a", "text", f"msg{i}")
            payloads = [m.payload for m in bus.poll("ch")]
            assert payloads == ["msg0", "msg1", "msg2", "msg3", "msg4"]

    def test_subscribe_receives_messages(self):
        received = []
        with SQLiteBus() as bus:
            bus.subscribe("ch", lambda msg: received.append(msg))
            bus.publish("ch", "a", "text", "hello")
            assert len(received) == 1
            assert received[0].payload == "hello"

    def test_subscribe_channel_isolation(self):
        received = []
        with SQLiteBus() as bus:
            bus.subscribe("ch1", lambda msg: received.append(msg))
            bus.publish("ch2", "a", "text", "wrong channel")
            assert len(received) == 0

    def test_unsubscribe(self):
        received = []
        with SQLiteBus() as bus:
            sub_id = bus.subscribe("ch", lambda msg: received.append(msg))
            bus.publish("ch", "a", "text", "msg1")
            bus.unsubscribe(sub_id)
            bus.publish("ch", "a", "text", "msg2")
            assert len(received) == 1

    def test_multiple_subscribers(self):
        received_a = []
        received_b = []
        with SQLiteBus() as bus:
            bus.subscribe("ch", lambda msg: received_a.append(msg))
            bus.subscribe("ch", lambda msg: received_b.append(msg))
            bus.publish("ch", "sender", "text", "broadcast")
            assert len(received_a) == 1
            assert len(received_b) == 1

    def test_channels_empty(self):
        with SQLiteBus() as bus:
            assert bus.channels() == []

    def test_channels_lists_all(self):
        with SQLiteBus() as bus:
            bus.publish("beta", "a", "text", "x")
            bus.publish("alpha", "a", "text", "x")
            bus.publish("gamma", "a", "text", "x")
            assert bus.channels() == ["alpha", "beta", "gamma"]

    def test_metadata_roundtrip(self):
        with SQLiteBus() as bus:
            meta = {"priority": "high", "tags": ["a", "b"]}
            bus.publish("ch", "a", "text", "x", metadata=meta)
            assert bus.poll("ch")[0].metadata == meta

    def test_context_manager_closes(self):
        bus = SQLiteBus()
        with bus:
            bus.publish("ch", "a", "text", "x")
        with pytest.raises(sqlite3.ProgrammingError):
            bus.publish("ch", "a", "text", "y")

    def test_repr(self):
        bus = SQLiteBus()
        assert "SQLiteBus" in repr(bus)
        assert ":memory:" in repr(bus)
        bus.close()


class TestSQLiteBusFileBacked:
    """Tests for SQLiteBus with file-backed database."""

    def test_persistence_across_instances(self, tmp_path):
        db_file = tmp_path / "test.db"
        with SQLiteBus(db_file) as bus:
            bus.publish("ch", "a", "text", "persistent-msg")

        with SQLiteBus(db_file) as bus:
            msgs = bus.poll("ch")
            assert len(msgs) == 1
            assert msgs[0].payload == "persistent-msg"

    def test_repr_with_path(self, tmp_path):
        db_file = tmp_path / "test.db"
        bus = SQLiteBus(db_file)
        assert str(db_file) in repr(bus)
        bus.close()
