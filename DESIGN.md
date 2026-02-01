# Agent Messaging Hub - 设计文档

## 项目概述

一个面向 LLM/Agent 的消息中枢，支持多种消息队列后端（Kafka/RocketMQ/RabbitMQ），用于：
- Agent 间通信
- 笔记/备忘录
- 任务追踪
- 记忆存储

---

## 设计问题

请在每个问题下方填写你的回答，我会根据回答完善设计。

### 1. 消息后端优先级

你希望首先支持哪个后端？后续扩展顺序是什么？

选项：
- A) RabbitMQ → Kafka → RocketMQ
- B) Kafka → RocketMQ → RabbitMQ
- C) Redis Streams（最简单）→ 再扩展到上述三个
- D) 其他

**你的回答：** 
Redis (Valkey) -> RabbitMQ -> RocketMQ
mvp的话只需要最简单的一个就行，找最成熟的，反正我们部署后端都是docker，然后通过用户提供的url进行后端访问。

对每个agent来说它看到就是一组tool，同时我们在后续的agent sdk设计中也会手动调用这些tool去进行部分的手动管理（比如监督、对话、调用等等），你觉得如何？

---

### 2. 消息持久化需求

Agent 的消息/记忆需要持久化吗？

- A) 全部持久化（重启后可恢复）
- B) 部分持久化（只有 memory/notes 需要）
- C) 纯内存模式优先，持久化作为可选功能
- D) 其他

**你的回答：**
持久化是必须的，我希望持久化的目的是
1. 可以被人看到和理解，这样我们可以“白盒化”这个机制。我希望持久化的模式可选：binary（最压缩），或者md文件
2. 重启可恢复
3. 当然我们在测试的时候持久化可以做的很简单

---

### 3. Agent 通信模式

你预期的主要通信模式是什么？

- A) 点对点（Agent A → Agent B）
- B) 发布订阅（一个 Agent 广播，多个订阅）
- C) 任务队列（多个 Worker 竞争消费）
- D) 混合模式（都需要）

**你的回答：**
我希望每个接入这个系统的agent都有如下的一些能力：
- 有一个自己的channel做history记录，这个部分我们可能是手动在sdk层面控制进行对话和工作记录的插入
- 有一个自己的notebook，用来记录thoughts或者一些重要的东西，或者当成一个scratch pad，可以fire and forget的东西，必要时候可以回查
- 有一个memory channel，我们可能要区分不同记忆类型？但是也可以通过增加管理agent来接入这个channel做，可能我们需要讨论一下
- 能看到一些公开的广播频道，比如公开的任务列表，公开的agent成员列表，公开的话题列表等等
- 可以选择创建“群”对话，多个agent在里面交流，类似slack channel
- 可以选择创建点对点对话

---

### 4. 消息类型

你需要哪些消息类型？请勾选或补充：

- [ ] TaskRequest（任务请求）
- [ ] TaskResult（任务结果）
- [ ] Memory（记忆/上下文）
- [ ] Note（笔记/备忘）
- [ ] Heartbeat（心跳/存活检测）
- [ ] Event（通用事件）
- [ ] 其他：___

**你的回答：**
感觉根据上面的通信模式预期，可能我们都得要
---

### 5. 同步 vs 异步

Agent 发送消息后的行为：

- A) 纯异步（fire-and-forget）
- B) 需要支持 request-reply 模式（发送后等待响应）
- C) 两者都需要

**你的回答：**
我觉得应该是异步吧，类似于人发slack，或者email，发出去也是不知道别人什么时候看的。但是呢，我们需要设计一个提醒机制，就像消息软件和邮件客户端一样，提醒模型现在有新的消息了。模型自主决定什么时候去看

但是可能对于一些及时对话场景，或者自身的消息召回（比如memory查询？可能我搞混了，你要帮我梳理）我们需要等待机制？

---

### 6. 序列化格式

消息体的序列化格式偏好：

- A) JSON（简单，可读性好）
- B) MessagePack（紧凑，比 JSON 快）
- C) Protobuf（强类型，需要预定义 schema）
- D) 可插拔（默认 JSON，支持扩展）

**你的回答：**
这个是什么时候要考虑呢？你先给我介绍一下。我理解之后才能回答。不过我觉得考虑到我们假设有多个agent的大量消息接入，性能很重要，稳定也很重要。因为模型见到的只有tool，tool client接入的时候可能是远程的。

---

### 7. API 风格

你偏好的 Python API 风格：

**选项 A - 简洁风格：**
```python
hub = Hub("rabbitmq://localhost")
hub.send("tasks", {"action": "summarize", "text": "..."})
msg = hub.receive("results")
```

**选项 B - 显式风格：**
```python
hub = Hub(backend=RabbitMQBackend(host="localhost"))
producer = hub.create_producer("tasks")
consumer = hub.create_consumer("results")
producer.send(TaskRequest(...))
```

**选项 C - 装饰器风格：**
```python
@hub.handler("tasks")
async def handle_task(msg: TaskRequest) -> TaskResult:
    return TaskResult(...)
```

- A / B / C / 混合？

**你的回答：**
风格可能要等上面的讨论确定大概之后才能决定。我可能是要做一个hub和client分离的，对于模型来说他们只看到client，server上放hub

---

### 8. 并发模型

- A) 同步阻塞（简单，适合脚本）
- B) asyncio（适合高并发）
- C) 两者都支持

**你的回答：**
并发是指什么阶段的并发？模型通过client tool发给hub还是什么？

---

### 9. 错误处理

消息处理失败时：

- A) 简单重试 + 丢弃
- B) 死信队列（DLQ）
- C) 可配置策略

**你的回答：**
消息处理失败是指？

---

### 10. 额外功能优先级

请排序（1=最重要，5=最不重要）：

- [ ] 消息追踪/链路追踪
- [ ] 消息过期/TTL
- [ ] 优先级队列
- [ ] 消息去重
- [ ] 延迟消息

**你的回答：**
这个你也得从上面我的需求上帮我分析一下，我不是特别懂

---

### 11. 部署环境

目标运行环境：

- A) 本地开发为主
- B) Docker/K8s
- C) 云服务（AWS/阿里云等托管 MQ）
- D) 都要考虑

**你的回答：**
我希望同时提供cli和docker，docker作为cli的封装。这样我们可以适应各种情况。

---

### 12. 与现有系统集成

是否需要与现有系统集成？如果有，请描述：

**你的回答：**
我的设计是基于openapi或者mcp接口暴露给模型的。所以我们应该就考虑一个是后端hub server怎么设计，一个是client toolset怎么设计
---

## 下一步

填写完成后请告诉我，我会：
1. 根据你的回答生成详细的技术设计
2. 设计 middleware 抽象层架构
3. 创建项目骨架代码
