# Piazza

[![CI](https://github.com/Oaklight/piazza/actions/workflows/ci.yml/badge.svg)](https://github.com/Oaklight/piazza/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Oaklight/piazza)

English Version | [中文版](README_zh.md)

A lightweight message bus for multi-agent AI collaboration — the town square (广场) where agents meet.

## Overview

Piazza provides structured, persistent communication channels for AI agents. Instead of point-to-point RPC or shared memory, agents interact through named channels with pub/sub semantics, cursor-based polling, and built-in identity management.

```
Backend (storage)  →  Bus (routing)  →  Client SDK (agent API)
   SQLite / Memory       pub/sub           identity, cursors,
                         channels           DMs, notes, memory
```

## Features

- **Channel-based messaging** — named channels with pub/sub, cursor-tracked polling, and message ordering via monotonic UUIDs
- **Pluggable storage** — `SQLiteBackend` (persistent, WAL mode) and `MemoryBackend` (ephemeral, testing); protocol-based, easy to extend
- **Client SDK** — `PiazzaClient` with agent identity, cursor persistence across sessions, and authentication (register/reconnect/secret hashing)
- **Semantic APIs** — DMs, broadcast channels, notes (with tags), thoughts (chain-of-thought logging), memory (store/recall), notifications
- **Admin panel** — built-in HTTP dashboard with REST API for stats, channel browsing, message inspection, and throughput monitoring
- **Flexible connection** — connect via Bus object, file path (SQLite), or `:memory:` string; URL schemes (`http://`, `redis://`) reserved for future transports
- **Zero runtime dependencies** — pure Python, stdlib only

## Quick Start

```python
from piazza import PiazzaClient

# In-memory bus (for testing)
with PiazzaClient(":memory:", "agent-alpha") as alice:
    alice.channel_send("general", "hello everyone!")
    alice.note_write("remember to check logs", tags=["ops"])
    alice.thought_record("planning", "next steps", "need to coordinate with bob")

# SQLite-backed (persistent)
with PiazzaClient("/tmp/piazza.db", "agent-alpha") as alice:
    alice.dm_send("agent-beta", "ready to sync?")

# Multi-agent collaboration
from piazza import Bus, MemoryBackend

bus = Bus(backend=MemoryBackend())

alice = PiazzaClient(bus, "agent-alice")
bob = PiazzaClient(bus, "agent-bob")

alice.dm_send("agent-bob", "PR is ready for review")
messages = bob.dm_read("agent-alice")  # ["PR is ready for review"]

alice.close()
bob.close()
bus.close()
```

## Architecture

Piazza follows a layered architecture inspired by messaging middleware, adapted for AI agent workflows:

| Layer | Component | Role |
|-------|-----------|------|
| **Storage** | `Backend` protocol | Persistent or ephemeral message storage (`SQLiteBackend`, `MemoryBackend`) |
| **Routing** | `Bus` | Channel management, pub/sub dispatch, UUID generation |
| **Transport** | `Transport` protocol | Abstraction for local vs. remote bus access (`LocalTransport`) |
| **Agent API** | `PiazzaClient` | Identity, cursors, auth, semantic messaging APIs |
| **Admin** | `AdminServer` | HTTP dashboard + REST API for monitoring |

For detailed design rationale, see [DESIGN_EN.md](docs/DESIGN_EN.md).

## Installation

Requires **Python >= 3.10**.

```bash
pip install piazza
```

Or from source:

```bash
git clone https://github.com/Oaklight/piazza.git
cd piazza
pip install -e ".[dev]"
```

## Client SDK API

### Core Operations

| Method | Description |
|--------|-------------|
| `channel_send(channel, content)` | Send message to a channel |
| `channel_read(channel)` | Read messages (no cursor advance) |
| `channel_poll(channel)` | Poll new messages (advances cursor) |
| `channel_list()` | List all channels |

### Semantic APIs

| Method | Description |
|--------|-------------|
| `dm_send(target, content)` | Send direct message |
| `dm_read(peer)` | Read DM conversation |
| `note_write(content, tags=)` | Write a note with optional tags |
| `note_read(tags=)` | Read notes, optionally filtered by tags |
| `thought_record(mode, focus, content)` | Record chain-of-thought |
| `thought_read()` | Read thought history |
| `memory_store(content)` | Store a memory |
| `memory_recall(query)` | Recall memories by keyword |
| `broadcast_list()` / `broadcast_read(topic)` | Browse broadcast channels |
| `notification_check()` | Poll notifications |

### Authentication

```python
# Register new agent (returns client + secret)
client, secret = PiazzaClient.register(bus, "agent-id")

# Reconnect with secret
client = PiazzaClient(bus, "agent-id", secret=saved_secret)
```

### Admin Panel

```python
from piazza import SQLiteBus

bus = SQLiteBus("piazza.db")
info = bus.start_admin(port=8741)
print(f"Dashboard: {info.url}")
# Visit http://localhost:8741 for the web UI
```

## Roadmap

- [ ] **RemoteTransport** — HTTP/WebSocket client-server mode
- [ ] **Message TTL** — automatic expiry and cleanup
- [ ] **Semantic memory recall** — vector embedding search
- [ ] **Channel ACL** — per-channel access control
- [ ] **Redis/AMQP backends** — distributed storage
- [ ] **Async API** — native async/await support
- [ ] **Federation** — cross-instance communication ([#4](https://github.com/Oaklight/piazza/issues/4))

## Academic Context

Piazza is the reference implementation for Chapter 9 of a dissertation on enabling agentic AI at scale through decoupled abstractions. The design emphasizes protocol-based interfaces, pluggable components, and a clear separation between transport, storage, and agent-level semantics.

## License

MIT — see [LICENSE](LICENSE) for details.
