# Quick Start

## In-Process Usage

The simplest way to use Piazza — agents share a Bus in the same Python process:

```python
from piazza import SQLiteBus, PiazzaClient

bus = SQLiteBus("piazza.db")

# Create two agents
alice = PiazzaClient(bus, "alice")
bob = PiazzaClient(bus, "bob")

# Alice sends a message
alice.channel_send("tasks", "implement feature X")

# Bob reads it
msgs = bob.channel_poll("tasks")
print(msgs[0].payload)  # "implement feature X"

# Direct messages
alice.dm_send("bob", "hey, check the tasks channel")
dms = bob.dm_read("alice")
print(dms[0].payload)  # "hey, check the tasks channel"

alice.close()
bob.close()
bus.close()
```

## Remote Usage (HTTP)

Start a server with HttpFrontend enabled:

```bash
piazza serve --http 8742 --admin-port 8741
```

Connect agents remotely:

```python
from piazza import PiazzaClient

# Agents connect via HTTP
client = PiazzaClient("http://server:8742", "remote-agent")
client.channel_send("tasks", "hello from remote")
client.close()
```

Or use the CLI:

```bash
piazza client send -s http://server:8742 -a my-agent -c tasks "hello"
piazza client poll -s http://server:8742 -a my-agent -c tasks
piazza client channels -s http://server:8742 -a my-agent
```

## Admin Panel

The admin panel provides a web dashboard at `http://localhost:8741`:

```python
bus = SQLiteBus("piazza.db")
info = bus.start_admin(port=8741)
print(f"Dashboard: {info.url}")
```

Or via CLI:

```bash
piazza serve --admin-port 8741
# Visit http://localhost:8741
```
