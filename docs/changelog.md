# Changelog

## Unreleased

### Added

- **CLI subcommands**: `piazza serve` (with `--http`, `--irc` frontends) and `piazza client` (send/poll/channels/dm)
- **HttpFrontend**: REST API + SSE push for remote agent access (`/v1/publish`, `/v1/query`, `/v1/subscribe`)
- **IrcFrontend**: bidirectional bridge between piazza channels and IRC (optional `piazza[irc]` extra)
- **PiazzaServer**: orchestrator for Bus + multiple Frontends
- **HttpTransport**: client-side HTTP transport — `PiazzaClient("http://...")` just works
- **Transport protocol**: `subscribe`/`unsubscribe` promoted to protocol contract
- **PiazzaClient SDK**: identity management, cursor tracking, DMs, notes, memory, thoughts
- **Admin panel**: web dashboard + REST API for monitoring
- **Security hardening**: configurable request body limits, query caps, CORS origin
- **Reliability**: atomic drop counter for SSE slow consumers, WAL retry logging

### Fixed

- SQLite WAL `journal_mode` lock contention on concurrent cold-start
- Signal handler safety — cleanup in main thread, not signal context
- Token redaction in CLI logs (`sk-abcd1...xyz9`)
- IRC reconnect duplicate subscription bug
- UTF-8 safe truncation for IRC message length limits

### Refactored

- Backend architecture: `Backend` protocol with `SQLiteBackend` / `MemoryBackend`
- Admin handlers split into `routes/` subpackage
- Pre-commit config aligned with toolregistry

## 0.0.1a999 (2026-04-14)

- Initial MVP with SQLite-backed message bus
- Modular architecture with Backend and Serializer protocols
- SQLiteBackend and MemoryBackend implementations
- JSONSerializer implementation
