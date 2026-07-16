"""piazza-client — lightweight agent SDK for piazza message bus."""

__version__ = "0.1.0"

from piazza_client.client import PiazzaClient
from piazza_client.transport import HttpTransport, PiazzaAPIError
from piazza_client.types import Message

__all__ = [
    "HttpTransport",
    "Message",
    "PiazzaAPIError",
    "PiazzaClient",
]
