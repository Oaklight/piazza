"""Server-side frontend adapters for piazza.

Frontends expose a Bus over different protocols (HTTP, WebSocket, IRC, etc.).
Multiple frontends can attach to the same Bus simultaneously.
"""

from piazza.frontends.base import Frontend
from piazza.frontends.http import HttpFrontend


def __getattr__(name: str):
    """Lazy import for optional frontends (avoids hard dependency on extras)."""
    if name == "IrcFrontend":
        from piazza.frontends.irc import IrcFrontend

        return IrcFrontend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Frontend", "HttpFrontend", "IrcFrontend"]
