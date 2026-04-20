"""piazza - Agent messaging hub for multi-agent collaboration."""

__version__ = "0.0.1a999"

from piazza.backends import MemoryBackend, SQLiteBackend
from piazza.bus import Bus, SQLiteBus
from piazza.client import PiazzaClient
from piazza.protocols import Backend, MessageBus, Serializer
from piazza.serializers import JSONSerializer
from piazza.types import Message

# Backward compatibility aliases (deprecated, will be removed)
SQLiteStorage = SQLiteBackend
MemoryStorage = MemoryBackend
StorageBackend = Backend

__all__ = [
    "Backend",
    "Bus",
    "JSONSerializer",
    "MemoryBackend",
    "MemoryStorage",
    "Message",
    "MessageBus",
    "PiazzaClient",
    "SQLiteBackend",
    "SQLiteBus",
    "SQLiteStorage",
    "Serializer",
    "StorageBackend",
]
