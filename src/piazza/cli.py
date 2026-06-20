"""Command-line interface for piazza server.

Starts a SQLiteBus with an admin panel HTTP server for monitoring
and interacting with the message bus.
"""

import argparse
import logging
import signal
import threading

from piazza import SQLiteBus, __version__


def _redact_token(token: str, head: int = 8, tail: int = 4) -> str:
    """Redact a token for safe logging, showing only head and tail.

    Args:
        token: The full token string.
        head: Number of leading characters to show.
        tail: Number of trailing characters to show.

    Returns:
        Redacted string like ``"sk-abcd1...xyz9"``.
    """
    if len(token) <= head + tail:
        return token
    return f"{token[:head]}...{token[-tail:]}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        prog="piazza",
        description="Piazza — agent messaging hub with admin panel.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-d",
        "--db",
        default="piazza.db",
        help="SQLite database path (default: piazza.db)",
    )
    parser.add_argument(
        "-H",
        "--host",
        default="127.0.0.1",
        help="Admin panel host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=8741,
        help="Admin panel port (default: 8741)",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Allow remote connections (binds to 0.0.0.0, enables auth)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Auth token for admin panel (auto-generated if --remote)",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Disable admin web UI, serve API only",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the piazza CLI.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).
    """
    args = parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("piazza")

    bus = SQLiteBus(args.db)
    logger.info("Bus started with database: %s", args.db)

    info = bus.start_admin(
        host=args.host,
        port=args.port,
        serve_ui=not args.no_ui,
        remote=args.remote,
        auth_token=args.token,
    )

    logger.info("Admin panel: %s", info.url)
    if info.token:
        logger.info("Auth token: %s", _redact_token(info.token))

    # Block until SIGINT/SIGTERM.
    # Signal handler only sets an Event — all cleanup runs in the main
    # thread to avoid reentrant calls into Bus/HTTPServer from a signal
    # handler context.
    stop = threading.Event()

    def handle_signal(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    stop.wait()
    logger.info("Shutting down...")
    bus.close()


if __name__ == "__main__":
    main()
