# Agent Messaging Hub - Design Document

## Project Overview

A messaging backbone system designed for LLMs/Agents, adopting a Hub-Client architecture. This project serves as the implementation of the **Messaging** component (Chapter 9) in the PhD dissertation *"Enabling Agentic AI at Scale through Decoupled Abstractions"*.

-   **Hub Server**: Manages message queues, persistence, and routing.
-   **Client SDK**: Encapsulates functionality into tools for agents (e.g., `send_message`, `read_channel`, `write_note`).

### Core Capabilities
-   Inter-Agent Communication (Group Chat / Direct Message)
-   Notebook/Scratch Pad
-   History Tracking
-   Memory Storage
-   Cognitive Process Recording (Thought)
-   Broadcast/Announcements

---

## Architectural Decisions

### 1. Adapter Layer

To achieve flexible configuration and extensibility (aligning with the **Decoupled Abstractions** philosophy), the system employs the **Adapter Pattern** (Shim/Middleware). All replaceable components interface through a unified abstraction.

```
┌─────────────────────────────────────────────────────┐
│                    Hub Server                        │
├─────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │  Backend    │  │  Storage    │  │ Serializer  │  │
│  │  Adapter    │  │  Adapter    │  │  Adapter    │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │
│         │                │                │         │
│         ▼                ▼                ▼         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │Redis/RabbitMQ│ │File/DB/S3  │  │JSON/MsgPack │  │
│  │/RocketMQ    │  │            │  │/Protobuf    │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  │
└─────────────────────────────────────────────────────┘
```

**Configuration** (via config field):
```yaml
hub:
  backend:
    type: redis  # redis | rabbitmq | rocketmq
    url: redis://localhost:6379
  storage:
    type: file  # file | sqlite | s3
    path: ./data
    format: markdown  # markdown | binary
  serializer:
    type: json  # json | msgpack | protobuf
```

### 2. Message Backend

**Priority Order**: Redis (Valkey) → RabbitMQ → RocketMQ

| Phase | Backend | Rationale |
|-------|---------|-----------|
| MVP | Redis Streams | Simplest, easy deployment, sufficient functionality. |
| Expansion | RabbitMQ | Mature MQ, rich routing capabilities. |
| Scale | RocketMQ | High throughput, distributed scenarios. |

**Deployment**: Docker containers, connecting to the backend via user-provided URLs.

**Adapter Interface**:
```python
class BackendAdapter(Protocol):
    def publish(self, channel: str, message: bytes) -> str: ...
    def subscribe(self, channel: str) -> Iterator[bytes]: ...
    def get_history(self, channel: str, limit: int) -> List[bytes]: ...
```

### 3. Persistence Strategy

**Requirements**:
1.  Human-readable and understandable ("White-box").
2.  Recoverable after restart.
3.  Simplifiable for testing.

**Implementation**:
| Mode | Format | Usage |
|------|--------|-------|
| Debug Mode | Markdown File | Development/Debugging, human-readable. |
| Production Mode | Binary (MessagePack) | Performance optimization, compressed storage. |

**Storage Adapter Interface**:
```python
class StorageAdapter(Protocol):
    def save(self, key: str, data: bytes) -> None: ...
    def load(self, key: str) -> Optional[bytes]: ...
    def list_keys(self, prefix: str) -> List[str]: ...
    def delete(self, key: str) -> None: ...
```

### 4. Serialization Format

| Format | Speed | Size | Readability | Scenario |
|--------|-------|------|-------------|----------|
| JSON | Slow | Large | Good | Debugging, small scale, white-box. |
| MessagePack | Fast | Small | Poor | Production environment. |
| Protobuf | Fastest | Smallest | Poor | Large scale, strong typing. |

**Decision**: Default to JSON, optional MessagePack for production.

**Serializer Adapter Interface**:
```python
class SerializerAdapter(Protocol):
    def encode(self, obj: Any) -> bytes: ...
    def decode(self, data: bytes) -> Any: ...
```

---

## Channel Types

| Channel Type | Naming Pattern | Usage | Access Control |
|--------------|----------------|-------|----------------|
| History | `history:{agent_id}` | Dialogue/Work log | Private, auto-written by SDK |
| Notebook | `notebook:{agent_id}` | Scratch pad / Thoughts (includes Thought) | Private, agent writes actively |
| Memory | `memory:{agent_id}` | Long-term memory (Semantic) | Private |
| Broadcast | `broadcast:{topic}` | Announcements, tasks, member lists | Public Read-only |
| Group | `group:{group_id}` | Group chat | Member Read/Write |
| DM | `dm:{agent_a}:{agent_b}` | Direct message | Both parties Read/Write |

### Cognitive Psychology Perspective: Notebook vs. Memory

Based on cognitive psychology memory classification, we designed two core private channels:

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent Cognitive System                    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────┐      ┌─────────────────────┐       │
│  │  Notebook Channel   │      │   Memory Channel    │       │
│  │  (Episodic/Working) │      │    (Semantic/LTM)   │       │
│  ├─────────────────────┤      ├─────────────────────┤       │
│  │ • Note (General)    │      │ • Memory (Fact/Know)│       │
│  │ • Thought (Process) │ ───▶ │   - fact            │       │
│  │   - reasoning       │Distill│   - experience      │       │
│  │   - planning        │      │   - decision        │       │
│  │   - reflection      │      │   - preference      │       │
│  │   - brainstorming   │      │                     │       │
│  ├─────────────────────┤      ├─────────────────────┤       │
│  │ Nature: Process,Temp│      │ Nature: Result,Perm  │       │
│  │ TTL: Auto-expire    │      │ TTL: Long-term      │       │
│  │ Analogy: Scratchpad │      │ Analogy: Notebook   │       │
│  └─────────────────────┘      └─────────────────────┘       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

| Dimension | Notebook (Episodic) | Memory (Semantic) |
|-----------|---------------------|-------------------|
| **Memory Type** | Episodic Memory / Working Memory | Semantic Memory / Long-term Memory |
| **Content** | Thinking process, temporary notes, drafts | Distilled conclusions, facts, knowledge |
| **Structure** | Can be messy streams of thought | Should be structured and concise |
| **Timeliness** | Disposable, auto-expirable | Persistently retained |
| **Usage** | Debugging, white-box, review | Future recall, knowledge reuse |
| **Analogy** | Calculations on scratch paper | Organized notes in a notebook |

**Design Decision**: Thoughts are stored in the Notebook channel because:
1.  The original intent of a Notebook is a "scratch pad / thoughts".
2.  Thoughts and Notes are both temporary, process-oriented content.
3.  They can be distinguished by message type (Note vs. Thought).

### Broadcast Channel Management

**Problem**: Who sends messages to the public board?

**Solution**: Introduce a **Moderator Mechanism**.

```
Agent A ─────┐
Agent B ─────┼──▶ [Submission Queue] ──▶ [Moderator] ──▶ [Broadcast Channel]
Agent C ─────┘           │                    │
                         │                    ▼
                         │              Review/Filter/Classify
                         │                    │
                         ▼                    ▼
                   broadcast:submissions   broadcast:announcements
                                           broadcast:tasks
                                           broadcast:members
```

-   **Submission Queue**: Any agent can submit to `broadcast:submissions`.
-   **Moderator Agent**: A system-builtin management agent responsible for reviewing, classifying, and publishing.
-   **MVP Simplification**: No Moderator initially; Broadcasts are manually published by admins or directly via API.

---

## Communication Patterns

### Sync vs. Async

| Scenario | Pattern | Description |
|----------|---------|-------------|
| Sending messages to others | Async fire-and-forget | Like sending Slack/Email. |
| Querying own memory/notebook | Sync query | Read operation, not message passing. |
| Waiting for replies | Async + Polling/Notify | Provide `check_notifications()` tool. |

**Core Principle**: Message sending is asynchronous, but data querying is synchronous.

### Notification Mechanism

**Decision**:
-   **Long-term Goal**: C (Active polling + Notification attached to return values).
-   **MVP Implementation**: A (`check_notifications()` tool, agent actively polls).

> 💡 **Note**: Scheme B (injecting notifications into all tool returns) requires either restricting to hub-related tools or dependency on agent framework support. MVP uses simple polling.

---

## Message Types

Based on communication pattern requirements, the following message types are supported:

| Type | Description | Usage |
|------|-------------|-------|
| `ChatMessage` | Chat message | Group chat / Direct message |
| `Note` | Note/Memo | Notebook scratch pad |
| `Memory` | Memory entry | Long-term memory storage |
| `Thought` | Cognitive process record | White-box thinking process (Ref: ThinkTool) |
| `Broadcast` | Broadcast message | Announcements / Tasks / Member lists |
| `TaskRequest` | Task request | Task distribution |
| `TaskResult` | Task result | Task completion feedback |
| `Notification` | Notification | New message / Event alert |
| `Heartbeat` | Heartbeat | Liveness check |

### Thought Type Design (Inspired by ThinkTool)

**Design Philosophy**: Transforming the agent's thinking process from a "Black Box" to a "White Box".

```python
class Thought:
    thinking_mode: str  # reasoning | planning | reflection | recalling | brainstorming | exploring
    focus_area: str     # Current problem/topic
    thought_process: str  # Detailed thinking process
    timestamp: datetime
    agent_id: str
```

**Storage Location**: Notebook channel (`notebook:{agent_id}`)

**Relationship with Other Types**:

| Type | Storage Location | Nature | Description |
|------|------------------|--------|-------------|
| **Note** | Notebook | Episodic | General notes, temporary records |
| **Thought** | Notebook | Episodic | Structured thinking process (CoT persistence) |
| **Memory** | Memory | Semantic | Distilled conclusions, facts, knowledge |

**Typical Workflow**:

```
[Thinking Process] ──record_thought()──▶ Notebook Channel
        │                                  (episodic)
        │
        ▼ Model summarizes/distills
        │
[Conclusion Reached] ──store_memory()──▶ Memory Channel
                                          (semantic)
```

---

## SDK Design

### Concurrency Model

| Mode | Description | Priority |
|------|-------------|----------|
| Sync | `msg = client.read_channel("xxx")` blocks waiting | MVP Implementation |
| Async | `msg = await client.read_channel("xxx")` | Future Support |

### Client Tools (MCP/OpenAPI)

```python
# ===== Message Sending =====
send_message(channel: str, content: str, type: MessageType) -> MessageId
send_dm(to_agent: str, content: str) -> MessageId

# ===== Message Reading =====
read_channel(channel: str, limit: int = 10) -> List[Message]
read_dm(with_agent: str, limit: int = 10) -> List[Message]

# ===== Notebook Operations (Episodic/Working Memory) =====
write_note(content: str, tags: List[str] = None) -> NoteId
read_notes(tags: List[str] = None, limit: int = 10) -> List[Note]

# Thought is also stored in Notebook channel (Structured Note)
record_thought(
    thinking_mode: str,  # reasoning | planning | reflection | recalling | ...
    focus_area: str,
    thought_process: str
) -> ThoughtId  # Underlying write to notebook:{agent_id}

read_thoughts(limit: int = 10) -> List[Thought]

# ===== Memory Operations (Semantic/Long-term Memory) =====
store_memory(content: str, type: str = "general") -> MemoryId
# type: general | fact | experience | decision | preference | ...
recall_memory(query: str, limit: int = 5) -> List[Memory]

# ===== Notifications =====
check_notifications() -> List[Notification]

# ===== Broadcast Discovery =====
list_broadcasts() -> List[BroadcastChannel]  # Get list of all public channels
read_broadcast(topic: str, limit: int = 10) -> List[Broadcast]

# ===== Group Management =====
create_group(name: str, members: List[str]) -> GroupId
join_group(group_id: str) -> bool
leave_group(group_id: str) -> bool
list_groups() -> List[Group]  # Get groups I have joined
```

### Message Interruption Mechanism (Agent SDK Level)

> 💡 **Note**: The message interruption mechanism is a function of the Agent SDK, not the Messaging Hub core. However, the Hub can provide support:

-   **Priority Field**: Messages can carry priority tags.
-   **Interrupt Channel**: A dedicated high-priority notification channel `interrupt:{agent_id}`.
-   **Agent SDK Implementation**: Check the interrupt channel before every tool call.

---

## Error Handling

**Message Processing Failure Scenarios**:
-   Network timeout.
-   Hub service unavailable.
-   Message format error.

**MVP Strategy**: Simple retry + return error to agent, letting the agent decide how to handle it.

Future expansion to Dead Letter Queues (DLQ) or configurable strategies.

---

## Extra Feature Priorities

| Feature | Priority | Description |
|---------|----------|-------------|
| Message Tracing | High | Application-level tracing + Backend monitoring tools (e.g., Redis Commander, RabbitMQ Management). |
| Message Expiry/TTL | Medium | Designed by channel type and session; refined after MVP. |
| Deduplication | Low | Distinguished by channel type requirements; not implemented initially. |
| Priority Queue | Low | See explanation below. |
| Delayed Message | Low | See explanation below. |

### Priority Queue Use Cases

Used when certain messages need prioritized processing:
-   **Urgent Tasks**: High-priority tasks jumping the queue.
-   **Message Interruption**: Important notifications delivered first.
-   **Resource Scheduling**: VIP agent requests responded to first.

**MVP**: Not needed. Current scenarios are simple; FIFO is sufficient.

### Delayed Message Use Cases

Used when a message needs to be triggered at a future point in time:
-   **Scheduled Reminders**: Remind self in 10 minutes.
-   **Retry Mechanism**: Retry N seconds after failure.
-   **Task Scheduling**: Schedule tasks for future execution.

**MVP**: Not needed. No explicit scheduled scenario requirements currently.

---

## Deployment

| Method | Description |
|--------|-------------|
| CLI | Run `agent-hub serve` directly. |
| Docker | Encapsulation of CLI, `docker run agent-hub`. |
| K8s | Helm chart (Future). |

> 📋 **Future Plan**: Design `docs/API.md` to define detailed API interfaces and naming conventions.

---

## Decision Summary

### Q1: Memory Management ✅

**Decision**: MVP uses simple key-value storage + type field classification.

Borrowing from ThinkTool design:
-   Unified `store_memory()` interface.
-   Distinguish memory types via `type` parameter (general/fact/experience/...).
-   Reuse ThinkTool's `recalling` mode for memory recall.

Future introduction of a specialized Memory Agent for intelligent management.

### Q2: Notification Mechanism ✅

**Decision**: MVP implements Scheme A (`check_notifications()` polling), long-term support for Scheme C.

### Q3: Channel Types ✅

**Decision**: 6 types satisfy requirements; added Broadcast Moderator mechanism description.

In MVP phase, Broadcasts are published directly by admins/API; Moderator Agent introduced later.

---

## Appendix: Original Requirements Discussion

<details>
<summary>Click to expand original Q&A record</summary>

### 1. Message Backend Priority

**Question**: Which backend do you want to support first? What is the subsequent expansion order?

**Answer**: Redis (Valkey) -> RabbitMQ -> RocketMQ. For MVP, just the simplest one is needed. Find the most mature one. Since we deploy backends via docker and access via user-provided URL. For each agent, it sees a set of tools. We will also manually call these tools in the subsequent agent sdk design for partial manual management.

### 2. Message Persistence Requirements

**Question**: Do Agent messages/memories need persistence?

**Answer**: Persistence is mandatory. The purpose is:
1. To be visible and understandable by humans, so we can "white-box" this mechanism. Persistence mode should be optional: binary (most compressed) or md file.
2. Recoverable after restart.
3. Persistence can be very simple during testing.

### 3. Agent Communication Patterns

**Question**: What is your expected main communication pattern?

**Answer**: Every agent accessing this system should have the following capabilities:
- A channel for history recording.
- A notebook for recording thoughts or important things.
- A memory channel.
- Ability to see public broadcast channels.
- Option to create "group" chats.
- Option to create point-to-point chats.

### 4. Message Types

**Answer**: Based on communication pattern expectations, we probably need all of them.

### 5. Sync vs Async

**Answer**: Should be asynchronous, similar to humans sending slack or email. But a notification mechanism is needed. For memory query, it is synchronous (read operation).

### 6. Serialization Format

**Answer**: Considering massive message access from multiple agents, performance and stability are important.

### 7. API Style

**Answer**: Hub and client should be separated. For models, they only see the client; the hub is on the server.

### 8. Concurrency Model

**Answer**: Clarified - refers to Client SDK implementation. Sync first, then asyncio support.

### 9. Error Handling

**Answer**: Clarified - MVP phase simple retry + return error to agent.

### 10. Extra Feature Priorities

**Answer**: Analyzed - Message Tracing (High) > TTL (Medium) > Deduplication/Priority/Delay (Low).

### 11. Deployment Environment

**Answer**: Provide both cli and docker. Docker as encapsulation of cli.

### 12. Integration with Existing Systems

**Answer**: Exposed to models based on openapi or mcp interfaces. Consider backend hub server design and client toolset design.

</details>

---

## Appendix: Design Feedback Record

<details>
<summary>Click to expand user feedback</summary>

### Message Backend
> We need to pay attention to providing a middleware/shim to interface with various backends, configured via config field.

**Action**: Added Adapter Layer design, including Backend/Storage/Serializer Adapters.

### Persistence Strategy
> This persistence might also need a flexible shim, configured via config field. Should it be called shim or middleware?

**Action**: Unified term "Adapter", which is a more accurate design pattern name. Added Storage Adapter interface.

### Serialization Format
> This also needs a shim/middleware, configured via config field.

**Action**: Added Serializer Adapter interface.

### Message Types
> Do we need to add a Thought type?

**Action**: Added Thought type, borrowing ThinkTool's design philosophy (white-boxing the thinking process).

### SDK Design
> How to know about public channels? Message interruption mechanism?

**Action**:
- Added `list_broadcasts()` API.
- Clarified that message interruption belongs to the Agent SDK level; Hub can provide priority/interrupt support.

### Extra Features
> Explanation of message tracing, deduplication, TTL, priority queue, delayed messages.

**Action**:
- Message Tracing: Application layer + Backend monitoring tools.
- Deduplication/TTL: Designed by channel type, refined after MVP.
- Priority Queue/Delayed Message: Added use case explanations.

### Q1 Memory Management
> Look at ThinkTool design.

**Action**: Borrowed ThinkTool's unified tool + mode parameter idea, adopting `store_memory()` + type field design.

### Q2 Notification Mechanism
> Long-term C, MVP first A.

**Action**: Confirmed decision, recorded in document.

### Q3 Broadcast Management
> Who sends messages to public board? Need moderation mechanism.

**Action**: Added Moderator mechanism design; MVP published directly by admin.

### Thought Storage Location
> Should we add a new channel or reuse memory or note to store thoughts?

**Action**:
- **Decision**: Reuse Notebook channel.
- **Rationale**: Notebook is Episodic/Working Memory, suitable for storing process-oriented Thoughts. Memory is Semantic/Long-term Memory, suitable for storing results.
- **Implementation**: Distinguish via message type (Note vs Thought) within the Notebook channel.

</details>