"""Command-line interface for piazza server.

Starts a SQLiteBus with an admin panel HTTP server for monitoring
and interacting with the message bus.
"""

import argparse
import logging
import signal
import sys

from piazza import SQLiteBus, __version__


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
        logger.info("Auth token: %s", info.token)

    # Block until SIGINT/SIGTERM
    shutdown = False

    def handle_signal(signum: int, _frame: object) -> None:
        nonlocal shutdown
        if not shutdown:
            shutdown = True
            sig_name = signal.Signals(signum).name
            logger.info("Received %s, shutting down...", sig_name)
            bus.close()
            sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    signal.pause()


if __name__ == "__main__":
    main()
