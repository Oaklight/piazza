# IRC Frontend

The IRC frontend bridges piazza channels to IRC channels, allowing agents and human operators to communicate via standard IRC clients.

## Installation

```bash
pip install piazza[irc]
```

## Usage

```python
from piazza import SQLiteBus
from piazza.frontends.irc import IrcFrontend

bus = SQLiteBus("piazza.db")

frontend = IrcFrontend(
    irc_host="irc.example.com",
    irc_port=6667,
    nickname="piazza-bot",
    channels=["tasks", "sync"],
)
frontend.attach(bus)
frontend.serve_forever()  # blocks
```

Or via CLI:

```bash
piazza serve --http 8742 \
    --irc irc.example.com:6667 \
    --irc-channels tasks sync \
    --irc-nick piazza-bot
```

## Channel Mapping

By default, piazza channel `tasks` maps to IRC channel `#tasks`. Use `channel_map` for explicit overrides:

```python
frontend = IrcFrontend(
    irc_host="irc.example.com",
    channels=["tasks"],
    channel_map={"tasks": "#project-tasks"},
)
```

## Message Flow

**IRC → piazza**: messages in mapped IRC channels are published to the corresponding piazza channel with `metadata={"source": "irc"}`.

**piazza → IRC**: messages published to piazza channels are forwarded to the mapped IRC channel as `sender: payload`. Messages originating from IRC are not echoed back.

## SSL/TLS

```python
frontend = IrcFrontend(
    irc_host="irc.libera.chat",
    irc_port=6697,
    use_ssl=True,
    channels=["tasks"],
)
```

Or via CLI:

```bash
piazza serve --irc irc.libera.chat:6697 --irc-ssl --irc-channels tasks
```
