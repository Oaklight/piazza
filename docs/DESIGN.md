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
- 广播/公告

---

## 架构决策

### 1. 消息后端

**优先级顺序**：Redis (Valkey) → RabbitMQ → RocketMQ

| 阶段 | 后端 | 理由 |
|------|------|------|
| MVP | Redis Streams | 最简单，部署方便，功能足够 |
| 扩展 | RabbitMQ | 成熟的 MQ，丰富的路由功能 |
| 大规模 | RocketMQ | 高吞吐，分布式场景 |

**部署方式**：Docker 容器，通过用户提供的 URL 连接后端。

### 2. 持久化策略

**需求**：
1. 可被人类阅读和理解（"白盒化"）
2. 重启后可恢复
3. 测试时可简化

**实现方案**：
| 模式 | 格式 | 用途 |
|------|------|------|
| 调试模式 | Markdown 文件 | 开发/调试，人类可读 |
| 生产模式 | Binary (MessagePack) | 性能优化，压缩存储 |

默认使用 JSON 格式（方便调试），生产环境可选 MessagePack。

### 3. 序列化格式

| 格式 | 速度 | 大小 | 可读性 | 适用场景 |
|------|------|------|--------|----------|
| JSON | 慢 | 大 | 好 | 调试、小规模、白盒化 |
| MessagePack | 快 | 小 | 差 | 生产环境 |
| Protobuf | 最快 | 最小 | 差 | 大规模、强类型 |

**决策**：默认 JSON，生产环境可选 MessagePack。

---

## Channel 类型

| Channel 类型 | 命名模式 | 用途 | 访问权限 |
|-------------|----------|------|----------|
| History | `history:{agent_id}` | 对话/工作记录 | 私有，SDK 自动写入 |
| Notebook | `notebook:{agent_id}` | scratch pad / thoughts | 私有，agent 主动写 |
| Memory | `memory:{agent_id}` | 长期记忆 | 私有 |
| Broadcast | `broadcast:*` | 公告、任务列表、成员列表 | 公开只读 |
| Group | `group:{group_id}` | 群聊 | 成员可读写 |
| DM | `dm:{agent_a}:{agent_b}` | 私聊 | 双方可读写 |

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

> ⚠️ **待确认**：选择以下方案之一
> - A) 提供 `check_notifications()` tool，agent 主动轮询
> - B) 在每次 tool 调用返回时附带通知摘要
> - C) 两者都支持

---

## 消息类型

基于通信模式需求，支持以下消息类型：

- [ ] `ChatMessage` - 聊天消息（群聊/私聊）
- [ ] `Note` - 笔记/备忘
- [ ] `Memory` - 记忆条目
- [ ] `Broadcast` - 广播消息
- [ ] `TaskRequest` - 任务请求
- [ ] `TaskResult` - 任务结果
- [ ] `Notification` - 通知
- [ ] `Heartbeat` - 心跳/存活检测

---

## SDK 设计

### 并发模型

| 模式 | 说明 | 优先级 |
|------|------|--------|
| 同步 | `msg = client.read_channel("xxx")` 阻塞等待 | MVP 先实现 |
| 异步 | `msg = await client.read_channel("xxx")` | 后续支持 |

### Client Tools（MCP/OpenAPI）

```python
# 消息发送
send_message(channel: str, content: str, type: MessageType) -> MessageId
send_dm(to_agent: str, content: str) -> MessageId

# 消息读取
read_channel(channel: str, limit: int = 10) -> List[Message]
read_dm(with_agent: str, limit: int = 10) -> List[Message]

# Notebook 操作
write_note(content: str, tags: List[str] = None) -> NoteId
read_notes(tags: List[str] = None, limit: int = 10) -> List[Note]

# Memory 操作
store_memory(content: str, type: str = "general") -> MemoryId
recall_memory(query: str, limit: int = 5) -> List[Memory]

# 通知
check_notifications() -> List[Notification]

# 群组管理
create_group(name: str, members: List[str]) -> GroupId
join_group(group_id: str) -> bool
leave_group(group_id: str) -> bool
```

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

| 功能 | 优先级 | 理由 |
|------|--------|------|
| 消息追踪 | 高 | 调试 agent 行为需要 |
| 消息过期/TTL | 中 | notebook/scratch 可能需要自动清理 |
| 消息去重 | 低 | agent 可能重复发送，但影响不大 |
| 优先级队列 | 低 | MVP 不需要 |
| 延迟消息 | 低 | MVP 不需要 |

---

## 部署方式

| 方式 | 说明 |
|------|------|
| CLI | 直接运行 `agent-hub serve` |
| Docker | CLI 的封装，`docker run agent-hub` |
| K8s | Helm chart（后续） |

---

## 待确认问题

### Q1: Memory 管理

**建议方案**：MVP 先用简单的 key-value 存储，记忆分类交给 agent 自己在 content 里标记。后续再加专门的 memory agent。

> 📝 **请确认**：是否同意此方案？

### Q2: 通知机制

Agent 如何知道有新消息？

- A) 提供 `check_notifications()` tool，agent 主动轮询
- B) 在每次 tool 调用返回时附带通知摘要
- C) 两者都支持

> 📝 **请选择**：A / B / C

### Q3: Channel 类型确认

上述 6 种 channel 类型是否覆盖需求？需要增减吗？

> 📝 **请确认**：是否满足需求，或需要调整？

---

## 下一步

1. 确认上述待确认问题
2. 设计详细的 API Schema
3. 创建项目骨架代码
4. 实现 Redis Streams 后端
5. 开发 Client SDK

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