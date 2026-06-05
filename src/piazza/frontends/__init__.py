"""Server-side frontend adapters for piazza.

Frontends expose a Bus over different protocols (HTTP, WebSocket, IRC, etc.).
Multiple frontends can attach to the same Bus simultaneously.
"""

from piazza.frontends.base import Frontend
from piazza.frontends.http import HttpFrontend

__all__ = ["Frontend", "HttpFrontend"]
