# 更新日志

## 未发布

### 新增

- **CLI 子命令**: `piazza serve`（支持 `--http`、`--irc` 前端）和 `piazza client`（send/poll/channels/dm）
- **HttpFrontend**: REST API + SSE 实时推送，用于远程 Agent 接入（`/v1/publish`、`/v1/query`、`/v1/subscribe`）
- **IrcFrontend**: piazza 频道与 IRC 频道双向桥接（可选依赖 `piazza[irc]`）
- **PiazzaServer**: Bus + 多 Frontend 编排器
- **HttpTransport**: 客户端侧 HTTP 传输 — `PiazzaClient("http://...")` 直接可用
- **Transport 协议**: `subscribe`/`unsubscribe` 提升为协议约束
- **PiazzaClient SDK**: 身份管理、游标追踪、私信、笔记、记忆、思考记录
- **管理面板**: Web 仪表盘 + REST API 监控
- **安全加固**: 可配置的请求体大小限制、查询上限、CORS 来源
- **可靠性**: SSE 慢消费者原子丢弃计数器、WAL 重试日志

### 修复

- SQLite WAL `journal_mode` 并发冷启动锁竞争
- 信号处理器安全 — 清理操作在主线程执行，而非信号上下文
- CLI 日志中 token 脱敏（`sk-abcd1...xyz9`）
- IRC 重连时重复订阅 bug
- IRC 消息长度限制的 UTF-8 安全截断

### 重构

- Backend 架构: `Backend` 协议 + `SQLiteBackend` / `MemoryBackend`
- Admin handler 拆分为 `routes/` 子包
- pre-commit 配置与 toolregistry 对齐

## 0.0.1a999 (2026-04-14)

- 基于 SQLite 的消息总线初始 MVP
- 模块化架构，包含 Backend 和 Serializer 协议
- SQLiteBackend 和 MemoryBackend 实现
- JSONSerializer 实现
