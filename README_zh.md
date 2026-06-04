# Piazza

[![CI](https://github.com/Oaklight/piazza/actions/workflows/ci.yml/badge.svg)](https://github.com/Oaklight/piazza/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Oaklight/piazza)

[English Version](README_en.md) | 中文版

一个轻量级的多智能体 AI 协作消息总线 —— 智能体们的广场。

## 概览

Piazza 为 AI 智能体提供结构化、可持久化的通信通道。智能体通过命名通道（channel）进行交互，支持发布/订阅语义、基于游标的轮询和内置身份管理，而非点对点 RPC 或共享内存。

```
Backend（存储）  →  Bus（路由）  →  Client SDK（智能体 API）
   SQLite / Memory      发布/订阅         身份、游标、
                         通道              私信、笔记、记忆
```

## 特性

- **基于通道的消息传递** — 命名通道，支持发布/订阅、游标跟踪轮询，通过单调 UUID 保证消息有序
- **可插拔存储** — `SQLiteBackend`（持久化，WAL 模式）和 `MemoryBackend`（临时，测试用）；基于 Protocol，易于扩展
- **Client SDK** — `PiazzaClient` 提供智能体身份标识、跨会话游标持久化、认证机制（注册/重连/密钥哈希）
- **语义化 API** — 私信、广播通道、笔记（支持标签）、思维记录（思维链日志）、记忆（存储/检索）、通知
- **管理面板** — 内置 HTTP 仪表板，REST API 可查看统计数据、浏览通道、检查消息、监控吞吐量
- **灵活连接** — 通过 Bus 对象、文件路径（SQLite）或 `:memory:` 字符串连接；URL 协议（`http://`、`redis://`）预留给未来的传输层
- **零运行时依赖** — 纯 Python，仅使用标准库

## 快速开始

```python
from piazza import PiazzaClient

# 内存总线（测试用）
with PiazzaClient(":memory:", "agent-alpha") as alice:
    alice.channel_send("general", "大家好！")
    alice.note_write("记得检查日志", tags=["运维"])
    alice.thought_record("planning", "下一步", "需要和 bob 协调")

# SQLite 持久化
with PiazzaClient("/tmp/piazza.db", "agent-alpha") as alice:
    alice.dm_send("agent-beta", "准备好同步了吗？")

# 多智能体协作
from piazza import Bus, MemoryBackend

bus = Bus(backend=MemoryBackend())

alice = PiazzaClient(bus, "agent-alice")
bob = PiazzaClient(bus, "agent-bob")

alice.dm_send("agent-bob", "PR 已经准备好 review 了")
messages = bob.dm_read("agent-alice")  # ["PR 已经准备好 review 了"]

alice.close()
bob.close()
bus.close()
```

## 架构

Piazza 采用分层架构，灵感来自消息中间件，针对 AI 智能体工作流进行了适配：

| 层级 | 组件 | 职责 |
|------|------|------|
| **存储层** | `Backend` 协议 | 持久化或临时消息存储（`SQLiteBackend`、`MemoryBackend`） |
| **路由层** | `Bus` | 通道管理、发布/订阅分发、UUID 生成 |
| **传输层** | `Transport` 协议 | 本地 vs 远程总线访问的抽象（`LocalTransport`） |
| **智能体 API** | `PiazzaClient` | 身份、游标、认证、语义化消息 API |
| **管理层** | `AdminServer` | HTTP 仪表板 + REST API 监控 |

详细设计理念请参阅 [DESIGN.md](docs/DESIGN.md)。

## 安装

需要 **Python >= 3.10**。

```bash
pip install piazza
```

或从源码安装：

```bash
git clone https://github.com/Oaklight/piazza.git
cd piazza
pip install -e ".[dev]"
```

## Client SDK API

### 核心操作

| 方法 | 描述 |
|------|------|
| `channel_send(channel, content)` | 向通道发送消息 |
| `channel_read(channel)` | 读取消息（不推进游标） |
| `channel_poll(channel)` | 轮询新消息（推进游标） |
| `channel_list()` | 列出所有通道 |

### 语义化 API

| 方法 | 描述 |
|------|------|
| `dm_send(target, content)` | 发送私信 |
| `dm_read(peer)` | 读取私信对话 |
| `note_write(content, tags=)` | 写笔记，可选标签 |
| `note_read(tags=)` | 读取笔记，可按标签过滤 |
| `thought_record(mode, focus, content)` | 记录思维链 |
| `thought_read()` | 读取思维历史 |
| `memory_store(content)` | 存储记忆 |
| `memory_recall(query)` | 按关键词检索记忆 |
| `broadcast_list()` / `broadcast_read(topic)` | 浏览广播通道 |
| `notification_check()` | 轮询通知 |

### 认证

```python
# 注册新智能体（返回 client + 密钥）
client, secret = PiazzaClient.register(bus, "agent-id")

# 使用密钥重连
client = PiazzaClient(bus, "agent-id", secret=saved_secret)
```

### 管理面板

```python
from piazza import SQLiteBus

bus = SQLiteBus("piazza.db")
info = bus.start_admin(port=8741)
print(f"仪表板: {info.url}")
# 访问 http://localhost:8741 查看 Web UI
```

## 路线图

- [ ] **RemoteTransport** — HTTP/WebSocket 客户端-服务端模式
- [ ] **消息 TTL** — 自动过期与清理
- [ ] **语义化记忆检索** — 向量嵌入搜索
- [ ] **通道 ACL** — 按通道的访问控制
- [ ] **Redis/AMQP 后端** — 分布式存储
- [ ] **异步 API** — 原生 async/await 支持
- [ ] **联邦** — 跨实例通信（[#4](https://github.com/Oaklight/piazza/issues/4)）

## 学术背景

Piazza 是一篇博士论文第九章的参考实现，该章节探讨通过解耦抽象实现大规模智能体 AI。设计强调基于协议的接口、可插拔组件，以及传输、存储和智能体级语义之间的清晰分离。

## 许可证

MIT — 详情请参阅 [LICENSE](LICENSE)。
