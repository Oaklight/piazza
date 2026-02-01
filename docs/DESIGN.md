# Agent Messaging Hub - 设计文档

## 项目概述

一个面向 LLM/Agent 的消息中枢系统，采用 Hub-Client 架构：
- **Hub Server**：管理消息队列、持久化、路由
- **Client SDK**：封装成 tool 供 agent 调用（如 `send_message`, `read_channel`, `write_note` 等）

### 核心功能
- Agent 间通信（群聊/私聊）
- 笔记/备忘录（Notebook/Scratch Pad）
- 历史记录追踪
- 记忆存储（Memory）
- 认知过程记录（Thought）
- 广播/公告

---

## 架构决策

### 1. Adapter Layer（适配器层）

为了实现灵活的配置和扩展，系统采用 **Adapter 模式**（也称 Shim/Middleware），所有可替换组件通过统一接口对接：

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

**配置方式**（通过 config field）：
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

### 2. 消息后端

**优先级顺序**：Redis (Valkey) → RabbitMQ → RocketMQ

| 阶段 | 后端 | 理由 |
|------|------|------|
| MVP | Redis Streams | 最简单，部署方便，功能足够 |
| 扩展 | RabbitMQ | 成熟的 MQ，丰富的路由功能 |
| 大规模 | RocketMQ | 高吞吐，分布式场景 |

**部署方式**：Docker 容器，通过用户提供的 URL 连接后端。

**Adapter 接口**：
```python
class BackendAdapter(Protocol):
    def publish(self, channel: str, message: bytes) -> str: ...
    def subscribe(self, channel: str) -> Iterator[bytes]: ...
    def get_history(self, channel: str, limit: int) -> List[bytes]: ...
```

### 3. 持久化策略

**需求**：
1. 可被人类阅读和理解（"白盒化"）
2. 重启后可恢复
3. 测试时可简化

**实现方案**：
| 模式 | 格式 | 用途 |
|------|------|------|
| 调试模式 | Markdown 文件 | 开发/调试，人类可读 |
| 生产模式 | Binary (MessagePack) | 性能优化，压缩存储 |

**Storage Adapter 接口**：
```python
class StorageAdapter(Protocol):
    def save(self, key: str, data: bytes) -> None: ...
    def load(self, key: str) -> Optional[bytes]: ...
    def list_keys(self, prefix: str) -> List[str]: ...
    def delete(self, key: str) -> None: ...
```

### 4. 序列化格式

| 格式 | 速度 | 大小 | 可读性 | 适用场景 |
|------|------|------|--------|----------|
| JSON | 慢 | 大 | 好 | 调试、小规模、白盒化 |
| MessagePack | 快 | 小 | 差 | 生产环境 |
| Protobuf | 最快 | 最小 | 差 | 大规模、强类型 |

**决策**：默认 JSON，生产环境可选 MessagePack。

**Serializer Adapter 接口**：
```python
class SerializerAdapter(Protocol):
    def encode(self, obj: Any) -> bytes: ...
    def decode(self, data: bytes) -> Any: ...
```

---

## Channel 类型

| Channel 类型 | 命名模式 | 用途 | 访问权限 |
|-------------|----------|------|----------|
| History | `history:{agent_id}` | 对话/工作记录 | 私有，SDK 自动写入 |
| Notebook | `notebook:{agent_id}` | scratch pad / thoughts | 私有，agent 主动写 |
| Memory | `memory:{agent_id}` | 长期记忆 | 私有 |
| Broadcast | `broadcast:{topic}` | 公告、任务列表、成员列表 | 公开只读 |
| Group | `group:{group_id}` | 群聊 | 成员可读写 |
| DM | `dm:{agent_a}:{agent_b}` | 私聊 | 双方可读写 |

### Broadcast Channel 管理

**问题**：谁给公开 board 发消息？

**解决方案**：引入 **Moderator 机制**

```
Agent A ─────┐
Agent B ─────┼──▶ [Submission Queue] ──▶ [Moderator] ──▶ [Broadcast Channel]
Agent C ─────┘           │                    │
                         │                    ▼
                         │              审核/过滤/分类
                         │                    │
                         ▼                    ▼
                   broadcast:submissions   broadcast:announcements
                                           broadcast:tasks
                                           broadcast:members
```

- **Submission Queue**：任何 agent 都可以提交到 `broadcast:submissions`
- **Moderator Agent**：系统内置的管理 agent，负责审核、分类、发布
- **MVP 简化**：先不做 Moderator，Broadcast 由管理员手动发布或通过 API 直接发布

---

## 通信模式

### 同步 vs 异步

| 场景 | 模式 | 说明 |
|------|------|------|
| 发消息给别人 | 异步 fire-and-forget | 像发 Slack/Email |
| 查询自己的 memory/notebook | 同步查询 | 读操作，不是消息传递 |
| 等待别人回复 | 异步 + 轮询/通知 | 提供 `check_notifications()` tool |

**核心原则**：消息发送是异步的，但数据查询是同步的。

### 通知机制

**决策**：
- **长期目标**：C（主动轮询 + 返回时附带通知）
- **MVP 实现**：A（`check_notifications()` tool，agent 主动轮询）

> 💡 **说明**：B 方案需要在所有 tool 返回时注入通知，这要么限制在 hub 相关工具，要么依赖 agent 框架支持。MVP 阶段先用简单的轮询方式。

---

## 消息类型

基于通信模式需求，支持以下消息类型：

| 类型 | 说明 | 用途 |
|------|------|------|
| `ChatMessage` | 聊天消息 | 群聊/私聊 |
| `Note` | 笔记/备忘 | Notebook scratch pad |
| `Memory` | 记忆条目 | 长期记忆存储 |
| `Thought` | 认知过程记录 | 思考过程白盒化（参考 ThinkTool） |
| `Broadcast` | 广播消息 | 公告/任务/成员列表 |
| `TaskRequest` | 任务请求 | 任务分发 |
| `TaskResult` | 任务结果 | 任务完成反馈 |
| `Notification` | 通知 | 新消息/事件提醒 |
| `Heartbeat` | 心跳 | 存活检测 |

### Thought 类型设计（借鉴 ThinkTool）

**设计理念**：让 agent 的思考过程从"黑盒"变成"白盒"

```python
class Thought:
    thinking_mode: str  # reasoning | planning | reflection | recalling | brainstorming | exploring
    focus_area: str     # 当前思考的问题/主题
    thought_process: str  # 详细的思考过程
    timestamp: datetime
    agent_id: str
```

**与 Memory 的关系**：
- **Thought**：记录思考过程（过程性），可以是长且杂乱的
- **Memory**：记录结论/知识（结果性），应该是简洁结构化的

---

## SDK 设计

### 并发模型

| 模式 | 说明 | 优先级 |
|------|------|--------|
| 同步 | `msg = client.read_channel("xxx")` 阻塞等待 | MVP 先实现 |
| 异步 | `msg = await client.read_channel("xxx")` | 后续支持 |

### Client Tools（MCP/OpenAPI）

```python
# ===== 消息发送 =====
send_message(channel: str, content: str, type: MessageType) -> MessageId
send_dm(to_agent: str, content: str) -> MessageId

# ===== 消息读取 =====
read_channel(channel: str, limit: int = 10) -> List[Message]
read_dm(with_agent: str, limit: int = 10) -> List[Message]

# ===== Notebook 操作 =====
write_note(content: str, tags: List[str] = None) -> NoteId
read_notes(tags: List[str] = None, limit: int = 10) -> List[Note]

# ===== Memory 操作 =====
store_memory(content: str, type: str = "general") -> MemoryId
recall_memory(query: str, limit: int = 5) -> List[Memory]

# ===== Thought 操作（借鉴 ThinkTool） =====
record_thought(
    thinking_mode: str,  # reasoning | planning | reflection | recalling | ...
    focus_area: str,
    thought_process: str
) -> ThoughtId

# ===== 通知 =====
check_notifications() -> List[Notification]

# ===== Broadcast 发现 =====
list_broadcasts() -> List[BroadcastChannel]  # 获取所有公开 channel 列表
read_broadcast(topic: str, limit: int = 10) -> List[Broadcast]

# ===== 群组管理 =====
create_group(name: str, members: List[str]) -> GroupId
join_group(group_id: str) -> bool
leave_group(group_id: str) -> bool
list_groups() -> List[Group]  # 获取我加入的群组
```

### 消息打断机制（Agent SDK 层面）

> 💡 **说明**：消息打断机制是 Agent SDK 层面的功能，不属于 Messaging Hub 核心。但 Hub 可以提供支持：

- **Priority Field**：消息可携带优先级标记
- **Interrupt Channel**：专门的高优先级通知通道 `interrupt:{agent_id}`
- **Agent SDK 实现**：在每次 tool 调用前检查 interrupt channel

---

## 错误处理

**消息处理失败场景**：
- 网络超时
- Hub 服务不可用
- 消息格式错误

**MVP 策略**：简单重试 + 返回错误给 agent，让 agent 自己决定处理方式。

后续可扩展为死信队列（DLQ）或可配置策略。

---

## 额外功能优先级

| 功能 | 优先级 | 说明 |
|------|--------|------|
| 消息追踪 | 高 | 应用层追踪 + 后端监控工具（如 Redis Commander、RabbitMQ Management） |
| 消息过期/TTL | 中 | 按 channel 类型和 session 设计，MVP 后再细化 |
| 消息去重 | 低 | 按 channel 类型区分需求，暂不实现 |
| 优先级队列 | 低 | 见下方解释 |
| 延迟消息 | 低 | 见下方解释 |

### 优先级队列应用场景

当需要确保某些消息优先处理时使用：
- **紧急任务**：高优先级任务插队执行
- **消息打断**：重要通知优先送达
- **资源调度**：VIP agent 的请求优先响应

**MVP 不需要**：目前场景较简单，先按 FIFO 处理。

### 延迟消息应用场景

需要在未来某个时间点触发消息时使用：
- **定时提醒**：设置 10 分钟后提醒自己
- **重试机制**：失败后延迟 N 秒重试
- **任务调度**：安排未来执行的任务

**MVP 不需要**：当前没有明确的定时场景需求。

---

## 部署方式

| 方式 | 说明 |
|------|------|
| CLI | 直接运行 `agent-hub serve` |
| Docker | CLI 的封装，`docker run agent-hub` |
| K8s | Helm chart（后续） |

> 📋 **后续计划**：设计 `docs/API.md` 定义详细的 API 接口和命名规范。

---

## 决策总结

### Q1: Memory 管理 ✅

**决策**：MVP 采用简单 key-value 存储 + type 字段分类

借鉴 ThinkTool 设计思路：
- 统一的 `store_memory()` 接口
- 通过 `type` 参数区分记忆类型（general/fact/experience/...）
- 可复用 ThinkTool 的 `recalling` 模式进行记忆召回

后续可引入专门的 Memory Agent 进行智能管理。

### Q2: 通知机制 ✅

**决策**：MVP 实现 A 方案（`check_notifications()` 轮询），长期支持 C 方案。

### Q3: Channel 类型 ✅

**决策**：6 种类型满足需求，增加 Broadcast Moderator 机制说明。

MVP 阶段 Broadcast 由管理员/API 直接发布，后续引入 Moderator Agent。

---

## 下一步

1. ~~确认上述待确认问题~~ ✅
2. 设计详细的 API Schema（`docs/API.md`）
3. 创建项目骨架代码
4. 实现 Adapter 接口
5. 实现 Redis Streams 后端
6. 开发 Client SDK

---

## 附录：原始需求讨论

<details>
<summary>点击展开原始问答记录</summary>

### 1. 消息后端优先级

**问题**：你希望首先支持哪个后端？后续扩展顺序是什么？

**回答**：Redis (Valkey) -> RabbitMQ -> RocketMQ。MVP的话只需要最简单的一个就行，找最成熟的，反正我们部署后端都是docker，然后通过用户提供的url进行后端访问。对每个agent来说它看到就是一组tool，同时我们在后续的agent sdk设计中也会手动调用这些tool去进行部分的手动管理。

### 2. 消息持久化需求

**问题**：Agent 的消息/记忆需要持久化吗？

**回答**：持久化是必须的，目的是：
1. 可以被人看到和理解，这样我们可以"白盒化"这个机制。希望持久化的模式可选：binary（最压缩），或者md文件
2. 重启可恢复
3. 测试的时候持久化可以做的很简单

### 3. Agent 通信模式

**问题**：你预期的主要通信模式是什么？

**回答**：每个接入这个系统的agent都有如下的一些能力：
- 有一个自己的channel做history记录
- 有一个自己的notebook，用来记录thoughts或者一些重要的东西
- 有一个memory channel
- 能看到一些公开的广播频道
- 可以选择创建"群"对话
- 可以选择创建点对点对话

### 4. 消息类型

**回答**：根据通信模式预期，可能都需要。

### 5. 同步 vs 异步

**回答**：应该是异步，类似于人发slack或email。但需要设计一个提醒机制。对于memory查询是同步的（读操作）。

### 6. 序列化格式

**回答**：考虑到多agent大量消息接入，性能很重要，稳定也很重要。

### 7. API 风格

**回答**：要做一个hub和client分离的，对于模型来说他们只看到client，server上放hub。

### 8. 并发模型

**回答**：已澄清 - 指 Client SDK 实现方式，先做同步，后续加 asyncio 支持。

### 9. 错误处理

**回答**：已澄清 - MVP 阶段简单重试 + 返回错误给 agent。

### 10. 额外功能优先级

**回答**：已分析 - 消息追踪(高) > TTL(中) > 去重/优先级/延迟(低)。

### 11. 部署环境

**回答**：同时提供cli和docker，docker作为cli的封装。

### 12. 与现有系统集成

**回答**：基于openapi或者mcp接口暴露给模型。考虑后端hub server设计和client toolset设计。

</details>

---

## 附录：设计反馈记录

<details>
<summary>点击展开用户反馈</summary>

### 消息后端
> 我们需要注意提供一个对接各种后端的middleware/shim，通过config field配置

**处理**：添加了 Adapter Layer 设计，包含 Backend/Storage/Serializer 三个 Adapter。

### 持久化策略
> 这个持久化我们可能也要做个灵活的shim，通过config field配置。是应该叫做shim还是middleware？

**处理**：统一使用 "Adapter" 术语，这是更准确的设计模式命名。添加了 Storage Adapter 接口。

### 序列化格式
> 这个也得有个shim/middleware，通过config field配置

**处理**：添加了 Serializer Adapter 接口。

### 消息类型
> 是否需要添加Thought类型？

**处理**：添加了 Thought 类型，借鉴 ThinkTool 的设计理念（让思考过程白盒化）。

### SDK 设计
> 公开channel如何获知？消息打断机制？

**处理**：
- 添加了 `list_broadcasts()` API
- 说明了消息打断机制属于 Agent SDK 层面，Hub 可提供 priority/interrupt 支持

### 额外功能
> 消息追踪、去重、TTL、优先级队列、延迟消息的解释

**处理**：
- 消息追踪：应用层 + 后端监控工具
- 去重/TTL：按 channel 类型设计，MVP 后细化
- 优先级队列/延迟消息：添加了应用场景说明

### Q1 Memory 管理
> 看一下 ThinkTool 设计

**处理**：借鉴 ThinkTool 的统一工具+模式参数思路，采用 `store_memory()` + type 字段设计。

### Q2 通知机制
> 长期C，MVP先A

**处理**：确认决策，记录在文档中。

### Q3 Broadcast 管理
> 谁给公开board发消息？需要moderation机制

**处理**：添加了 Moderator 机制设计，MVP 先由管理员直接发布。

</details>