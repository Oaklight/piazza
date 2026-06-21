# Piazza

**Agent messaging hub for multi-agent collaboration.**

Piazza provides a lightweight, zero-dependency message bus for multi-agent systems. Agents communicate through channels using a simple publish/subscribe model, with pluggable backends for storage and frontends for network access.

## Key Features

- **Zero runtime dependencies** — pure Python stdlib
- **Three-layer architecture** — Backend (storage) ↔ Bus ↔ Frontend (access protocol)
- **Multiple backends** — SQLite (persistent) and Memory (ephemeral)
- **Remote transport** — HTTP REST API + SSE push notifications
- **IRC frontend** — bridge agent communication to IRC channels
- **Admin panel** — built-in web dashboard for monitoring
- **CLI** — `piazza serve` for server, `piazza client` for agent operations
- **PiazzaClient SDK** — identity management, cursor tracking, DMs, notes, memory

## Quick Example

```python
from piazza import SQLiteBus, PiazzaClient

# Start a bus
bus = SQLiteBus("piazza.db")

# Connect an agent
client = PiazzaClient(bus, "my-agent")
client.channel_send("tasks", "hello world")

# Read messages
msgs = client.channel_poll("tasks")
print(msgs[0].payload)  # "hello world"

client.close()
bus.close()
```

## Architecture

```
Agent ←→ PiazzaClient ←→ Transport ←→ Bus ←→ Backend
                              ↑
                          HttpFrontend / IrcFrontend
                              ↑
                        Remote Agents
```

## Installation

```bash
pip install piazza
```

See the [Installation Guide](usage/installation.md) for details.
