"""Shared test fixtures."""

from __future__ import annotations

import threading
import time

import pytest

from piazza import Bus, MemoryBackend, PiazzaServer
from piazza.frontends import HttpFrontend


@pytest.fixture()
def server_url():
    """Start a PiazzaServer with HttpFrontend on a random port, yield URL."""
    bus = Bus(backend=MemoryBackend())
    frontend = HttpFrontend(host="127.0.0.1", port=0)
    server = PiazzaServer(bus)
    server.add_frontend(frontend)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)

    host, port = frontend.address
    url = f"http://{host}:{port}"

    yield url

    server.shutdown()
