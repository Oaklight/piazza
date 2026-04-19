# piazza MVP Spec

**Date:** 2026-04-14
**Goal:** Minimal message bus that ctxweave needs for multi-agent context sync.

---

## Scope

### In (MVP)

- **SQLite backend** — zero external deps, single file, survives restarts
- **In-process library** — `bus = SQLiteBus("piazza.db")`, no server
- **Generic Message** — one type with a `msg_type` field for differentiation
- **Core operations**: publish, poll, subscribe (in-process callback), list channels
- **Sync API** — no async
- **Cross-process support** via shared SQLite file + polling

### Out (Future)

- Hub-Server architecture (Redis, RabbitMQ, etc.)
- Client SDK (PiazzaClient) with identity, cursor, channel naming
- Typed channel enforcement (history/notebook/memory/broadcast/group/DM)
- Moderator mechanism
- Message TTL / retention signals
- Priority queue / delayed messages
- Async API
- MCP/OpenAPI server exposure
- llm-rosetta IR integration

---

## API Surface

### Types

```python
@dataclass(frozen=True)
class Message:
    id: str              # UUID v7 (time-ordered)
    channel: str         # channel name (free-form string)
    sender: str          # agent ID
    msg_type: str        # "text" | "context_sync" | "notification" | "artifact" | ...
    payload: str         # content (JSON string or plain text)
    timestamp: str       # ISO 8601
    metadata: dict | None  # optional extra fields
```

### Protocol

```python
class MessageBus(Protocol):
    def publish(self, channel: str, sender: str, msg_type: str, payload: str,
                metadata: dict | None = None) -> str:
        """Publish a message. Returns message ID."""
        ...

    def poll(self, channel: str, after: str | None = None,
             limit: int = 100) -> list[Message]:
        """Get messages from channel. If `after` is a message ID, returns
        messages after that point. Returns oldest-first."""
        ...

    def subscribe(self, channel: str,
                  callback: Callable[[Message], None]) -> str:
        """Register in-process callback for new messages. Returns subscription ID.
        Callback is invoked synchronously during publish() in the same process."""
        ...

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription."""
        ...

    def channels(self) -> list[str]:
        """List all channels that have at least one message."""
        ...
```

### Implementation

```python
class SQLiteBus:
    def __init__(self, db_path: str | Path = ":memory:"):
        """Create bus. Use file path for persistence, \":memory:\" for testing."""
        ...
```

---

## Modular Architecture

The bus is composed of pluggable components via Protocol interfaces:

```
Bus (orchestrator)
├── Backend          → where messages are stored and delivered
│   ├── SQLiteBackend  (default, cross-process via WAL)
│   └── MemoryBackend  (testing)
├── Serializer       → how metadata is encoded
│   └── JSONSerializer (default, human-readable)
└── In-process pub/sub (built-in observer pattern)
```

### Plug Points

| Component | Protocol | MVP Default | Future Options |
|-----------|----------|-------------|----------------|
| Backend | `Backend` | `SQLiteBackend` | Redis, RabbitMQ, S3, PostgreSQL |
| Serializer | `Serializer` | `JSONSerializer` | MessagePack, Protobuf |
| Bus | `MessageBus` | `Bus` | Custom implementations |

### Usage

```python
from piazza import Bus, SQLiteBackend, MemoryBackend, JSONSerializer

# Default: SQLite + JSON (same as SQLiteBus())
bus = Bus()

# Explicit configuration
bus = Bus(
    backend=SQLiteBackend("workspace/.piazza.db"),
    serializer=JSONSerializer(),
)

# Convenience shorthand
bus = SQLiteBus("workspace/.piazza.db")

# Testing: pure in-memory
bus = Bus(backend=MemoryBackend())
```

### Adding a New Backend

Implement the `Backend` protocol:

```python
class RedisBackend:
    def store(self, message: Message) -> None: ...
    def query(self, channel: str, after: str | None = None, limit: int = 100) -> list[Message]: ...
    def list_channels(self) -> list[str]: ...
    def close(self) -> None: ...

bus = Bus(backend=RedisBackend("redis://localhost:6379"))
```

---

## How ctxweave Uses It

```python
from piazza import SQLiteBus

# Each agent gets a bus instance pointing to the same DB file
bus = SQLiteBus("workspace/.piazza.db")

# ctxweave push: announce new commits
bus.publish(
    channel="sync",
    sender="agent-a",
    msg_type="context_sync",
    payload='{"commit_ids": ["abc123", "def456"]}',
)

# ctxweave pull: check for new commits from others
new_messages = bus.poll(channel="sync", after=last_seen_id)
for msg in new_messages:
    if msg.sender != my_id:
        commits_to_pull = json.loads(msg.payload)["commit_ids"]
        # ... pull those commits ...

# In-process notification (optional, for same-process multi-agent)
bus.subscribe("sync", lambda msg: print(f"New sync from {msg.sender}"))
```

---

## Schema

```sql
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    sender TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    metadata TEXT  -- JSON or NULL
);

CREATE INDEX IF NOT EXISTS idx_channel_id ON messages (channel, id);
CREATE INDEX IF NOT EXISTS idx_channel_ts ON messages (channel, timestamp);
```

---

## Design Decisions

1. **Why SQLite, not Redis?** Zero deps. ctxweave already targets zero-dep philosophy. MVP piazza should match. SQLite supports cross-process access via WAL mode.

2. **Why no typed channels?** Channel semantics are the caller's responsibility. piazza just transports messages. This keeps the bus minimal and avoids piazza needing to know about ctxweave internals.

3. **Why sync, not async?** ctxweave's operations are synchronous. Adding async doubles the API surface for no MVP benefit.

4. **Why poll + subscribe?** Poll works cross-process (shared DB). Subscribe works in-process (observer pattern). Both are useful, neither is complex.

5. **Message ID as cursor** — `poll(after=id)` uses message ID (UUID v7, time-ordered) as cursor instead of timestamp. Avoids clock skew issues and is monotonically increasing.
