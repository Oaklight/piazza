# Architecture Overview

Piazza uses a three-layer symmetric architecture:

```
Backend (storage) ←→ Bus (routing) ←→ Frontend (access protocol)
```

## Layers

### Backend

Handles message persistence. Implements the `Backend` protocol (`store`, `query`, `list_channels`, `close`).

| Backend | Storage | Use Case |
|---|---|---|
| `SQLiteBackend` | SQLite WAL-mode file | Production, persistent |
| `MemoryBackend` | In-memory dict | Testing, ephemeral |

### Bus

Core message router. Holds a Backend and an in-process subscriber registry. Provides `publish`, `poll`, `subscribe`, `unsubscribe`, `channels`.

- `Bus` — composable, accepts any Backend
- `SQLiteBus` — convenience wrapper for `Bus(backend=SQLiteBackend(path))`

### Frontend

Exposes the Bus over a network protocol. Implements the `Frontend` protocol (`attach`, `serve_forever`, `shutdown`, `address`).

| Frontend | Protocol | Use Case |
|---|---|---|
| `HttpFrontend` | REST + SSE | Remote agent access |
| `IrcFrontend` | IRC | Human observation, IRC bots |

### Transport

Client-side counterpart to Frontends. Implements the `Transport` protocol.

| Transport | Connects To | Usage |
|---|---|---|
| `LocalTransport` | In-process Bus | `PiazzaClient(bus, "agent")` |
| `HttpTransport` | HttpFrontend | `PiazzaClient("http://...", "agent")` |

## Orchestration

`PiazzaServer` binds a Bus to one or more Frontends:

```python
server = PiazzaServer(bus)
server.add_frontend(HttpFrontend(port=8742))
server.add_frontend(IrcFrontend(irc_host="irc.example.com", channels=["tasks"]))
server.serve_forever()
```

## Data Flow

```
Agent A                          Agent B (remote)
   │                                │
   ├─ PiazzaClient                  ├─ PiazzaClient
   │    │                           │    │
   │    ├─ LocalTransport           │    ├─ HttpTransport
   │    │    │                      │    │    │
   │    │    ▼                      │    │    ▼
   │    │   Bus ◄──────────────── HttpFrontend
   │    │    │                           │
   │    │    ▼                           │
   │    │  SQLiteBackend                 │
   │    │    │                           │
   │    │    ▼                           │
   │    │  piazza.db                     │
   │    │                                │
   │    ├─ AdminServer (port 8741)       │
   │    │    └─ Web Dashboard            │
```
