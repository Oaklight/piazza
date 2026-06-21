# Changelog

## Unreleased

### Added

- **CLI subcommands**: `piazza serve` and `piazza client` (send/poll/channels/dm)
- **HttpFrontend**: REST API + SSE push for remote agent access
- **IrcFrontend**: bidirectional bridge between piazza channels and IRC
- **PiazzaServer**: orchestrator for Bus + multiple Frontends
- **HttpTransport**: client-side HTTP transport for remote connections
- **PiazzaClient SDK**: identity management, cursor tracking, DMs, notes, memory, thoughts
- **Admin panel**: web dashboard + REST API for monitoring
- **Security hardening**: request body limits, query caps, configurable CORS
- **Reliability**: atomic drop counter for SSE, WAL retry logging

### Fixed

- SQLite WAL journal_mode lock contention on concurrent cold-start
- Signal handler safety (cleanup in main thread, not signal context)
- Token redaction in CLI logs
