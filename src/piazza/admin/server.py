"""HTTP server for admin panel.

Provides the AdminServer class for running the admin panel
as a background HTTP server with optional authentication.
"""

import logging
import socket
import threading
from dataclasses import dataclass
from http.server import HTTPServer
from typing import TYPE_CHECKING

from .auth import SessionAuth
from .handlers import AdminRequestHandler

if TYPE_CHECKING:
    from piazza.bus import Bus
    from piazza.token_store import TokenStore

logger = logging.getLogger(__name__)


@dataclass
class AdminInfo:
    """Information about the running admin server.

    Attributes:
        host: The host address the server is bound to.
        port: The port number the server is listening on.
        url: The full URL to access the admin panel.
        password: The admin password (if auth is enabled).
    """

    host: str
    port: int
    url: str
    password: str | None


class AdminServer:
    """Admin panel HTTP server.

    Manages an HTTP server that provides a REST API and optional
    web UI for monitoring the piazza message bus.

    Args:
        bus: The Bus instance to monitor.
        host: Host address to bind to. Defaults to "127.0.0.1".
        port: Port number to listen on. Defaults to 8741.
        serve_ui: Whether to serve the admin UI at root path.
        remote: Whether to allow remote connections (binds to 0.0.0.0).
        auth_password: Optional admin password. Auto-generated if remote=True.
        token_store: Optional TokenStore for agent token management.

    Example:
        >>> from piazza import Bus
        >>> bus = Bus()
        >>> server = AdminServer(bus, port=8741)
        >>> info = server.start()
        >>> print(f"Admin panel at: {info.url}")
        >>> server.stop()
    """

    def __init__(
        self,
        bus: "Bus",
        host: str = "127.0.0.1",
        port: int = 8741,
        serve_ui: bool = True,
        remote: bool = False,
        auth_password: str | None = None,
        token_store: "TokenStore | None" = None,
        # Backward compat: accept auth_token as alias for auth_password
        auth_token: str | None = None,
    ) -> None:
        self._bus = bus
        self._host = "0.0.0.0" if remote else host
        self._port = port
        self._serve_ui = serve_ui
        self._remote = remote
        self._token_store = token_store

        # auth_token is the legacy name; auth_password takes precedence
        password = auth_password or auth_token
        if password is not None:
            self._auth: SessionAuth | None = SessionAuth(password)
        elif remote:
            self._auth = SessionAuth()
        else:
            self._auth = None

        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()

    def start(self) -> AdminInfo:
        """Start the server in a background thread.

        Returns:
            AdminInfo containing server details including URL and password.

        Raises:
            RuntimeError: If the server is already running.
        """
        if self._server is not None:
            raise RuntimeError("Server is already running")

        if self._port != 0:
            actual_port = self.find_available_port(self._host, self._port)
            self._port = actual_port

        handler_class = type(
            "BoundAdminRequestHandler",
            (AdminRequestHandler,),
            {
                "bus": self._bus,
                "auth": self._auth,
                "serve_ui": self._serve_ui,
                "token_store": self._token_store,
            },
        )

        self._server = HTTPServer((self._host, self._port), handler_class)
        # Get actual port (important when port=0, OS assigns)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()
        self._started.wait(timeout=5.0)

        display_host = "localhost" if self._host in ("0.0.0.0", "127.0.0.1") else self._host
        url = f"http://{display_host}:{self._port}"

        info = AdminInfo(
            host=self._host,
            port=self._port,
            url=url,
            password=self._auth.password if self._auth else None,
        )

        logger.info(f"Admin server started at {url}")
        if self._auth:
            logger.info(f"Admin password: {self._auth.password}")

        return info

    def _run_server(self) -> None:
        """Run the server (called in background thread)."""
        self._started.set()
        if self._server:
            self._server.serve_forever()

    def stop(self) -> None:
        """Stop the server. Safe to call if not running."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self._started.clear()
            logger.info("Admin server stopped")

        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def is_running(self) -> bool:
        """Check if server is running."""
        return self._server is not None and self._started.is_set()

    def get_info(self) -> AdminInfo | None:
        """Get server info if running."""
        if not self.is_running():
            return None

        display_host = "localhost" if self._host in ("0.0.0.0", "127.0.0.1") else self._host
        url = f"http://{display_host}:{self._port}"

        return AdminInfo(
            host=self._host,
            port=self._port,
            url=url,
            password=self._auth.password if self._auth else None,
        )

    @staticmethod
    def find_available_port(host: str, start_port: int) -> int:
        """Find an available port starting from start_port.

        Args:
            host: The host address to check.
            start_port: The port number to start searching from.

        Returns:
            An available port number.

        Raises:
            RuntimeError: If no available port found after 100 attempts.
        """
        for port in range(start_port, start_port + 100):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind((host, port))
                    return port
            except OSError:
                continue

        raise RuntimeError(f"Could not find available port in range {start_port}-{start_port + 99}")
