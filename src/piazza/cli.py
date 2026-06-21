"""Command-line interface for piazza server and client.

Subcommands:
    piazza serve   — start Bus + AdminServer (+ optional HttpFrontend)
    piazza client  — send/poll/list/dm via a remote HttpFrontend
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
import urllib.error

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


def _parse_host_port(value: str, default_host: str = "127.0.0.1") -> tuple[str, int]:
    """Parse a ``[HOST:]PORT`` string.

    Args:
        value: Either ``"PORT"`` or ``"HOST:PORT"``.
        default_host: Host to use when only port is given.

    Returns:
        Tuple of (host, port).

    Raises:
        argparse.ArgumentTypeError: If the value cannot be parsed.
    """
    if ":" in value:
        parts = value.rsplit(":", 1)
        try:
            return parts[0], int(parts[1])
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid host:port: {value!r}") from None
    try:
        return default_host, int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid port: {value!r}") from None


def _msg_to_json(msg) -> str:
    """Serialize a Message to a JSON string.

    Args:
        msg: A Message object.

    Returns:
        JSON string representation.
    """
    return json.dumps(
        {
            "id": msg.id,
            "channel": msg.channel,
            "sender": msg.sender,
            "msg_type": msg.msg_type,
            "payload": msg.payload,
            "timestamp": msg.timestamp,
            "metadata": msg.metadata,
        },
        ensure_ascii=False,
    )


# ── Argument Parsing ──────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with subcommands.

    Returns:
        Configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        prog="piazza",
        description="Piazza — agent messaging hub.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    sub = parser.add_subparsers(dest="command")

    # ── serve ─────────────────────────────────────────────────────
    serve = sub.add_parser("serve", help="Start the piazza server")
    serve.add_argument(
        "-d",
        "--db",
        default="piazza.db",
        help="SQLite database path (default: piazza.db)",
    )
    serve.add_argument(
        "--http",
        metavar="[HOST:]PORT",
        default=None,
        help="Enable HttpFrontend on [HOST:]PORT (e.g. 8742 or 0.0.0.0:8742)",
    )
    serve.add_argument(
        "--admin-port",
        type=int,
        default=8741,
        help="Admin panel port (default: 8741)",
    )
    serve.add_argument(
        "--remote",
        action="store_true",
        help="Allow remote connections for admin (binds to 0.0.0.0, enables auth)",
    )
    serve.add_argument(
        "--token",
        default=None,
        help="Auth token for admin panel (auto-generated if --remote)",
    )
    serve.add_argument(
        "--no-ui",
        action="store_true",
        help="Disable admin web UI, serve API only",
    )
    serve.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    serve.add_argument(
        "--irc",
        metavar="HOST:PORT",
        default=None,
        help="Enable IrcFrontend connecting to HOST:PORT (e.g. irc.example.com:6667)",
    )
    serve.add_argument(
        "--irc-nick",
        default="piazza-bot",
        help="IRC bot nickname (default: piazza-bot)",
    )
    serve.add_argument(
        "--irc-channels",
        nargs="*",
        default=None,
        help="Piazza channels to bridge to IRC (auto-mapped to #<name>)",
    )
    serve.add_argument(
        "--irc-ssl",
        action="store_true",
        help="Use SSL/TLS for IRC connection",
    )

    # ── client ────────────────────────────────────────────────────
    client = sub.add_parser("client", help="Interact with a remote piazza server")
    client_sub = client.add_subparsers(dest="action")

    # Shared arguments for all client actions
    _client_common = argparse.ArgumentParser(add_help=False)
    _client_common.add_argument(
        "-s",
        "--server",
        required=True,
        help="Server URL (e.g. http://localhost:8742)",
    )
    _client_common.add_argument(
        "-a",
        "--agent",
        required=True,
        help="Agent ID",
    )

    # client send
    send = client_sub.add_parser("send", parents=[_client_common], help="Send a message")
    send.add_argument("-c", "--channel", required=True, help="Target channel")
    send.add_argument("-t", "--type", default="chat", dest="msg_type", help="Message type")
    send.add_argument("message", help="Message content")

    # client poll
    poll = client_sub.add_parser("poll", parents=[_client_common], help="Poll messages")
    poll.add_argument("-c", "--channel", required=True, help="Channel to poll")
    poll.add_argument(
        "-n",
        "--limit",
        type=int,
        default=10,
        help="Max messages to return (default: 10)",
    )
    poll.add_argument(
        "--follow",
        action="store_true",
        help="Keep listening for new messages via SSE",
    )

    # client channels
    client_sub.add_parser("channels", parents=[_client_common], help="List channels")

    # client dm
    dm = client_sub.add_parser("dm", parents=[_client_common], help="Send a direct message")
    dm.add_argument("--to", required=True, dest="to_agent", help="Recipient agent ID")
    dm.add_argument("message", help="Message content")

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    When no subcommand is given, defaults to ``serve``.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Parsed arguments namespace.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Default to serve when no subcommand given
    if args.command is None:
        return parser.parse_args(["serve"] + (argv if argv is not None else []))

    return args


# ── Subcommand Handlers ───────────────────────────────────────────


def _cmd_serve(args: argparse.Namespace) -> None:
    """Handle the ``serve`` subcommand.

    Args:
        args: Parsed arguments namespace.
    """
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("piazza")

    bus = SQLiteBus(args.db)
    logger.info("Bus started with database: %s", args.db)

    # Start HttpFrontend if requested
    http_frontend = None
    if args.http:
        from piazza.frontends.http import HttpFrontend

        host, port = _parse_host_port(args.http)
        http_frontend = HttpFrontend(host=host, port=port)
        http_frontend.attach(bus)
        http_thread = threading.Thread(
            target=http_frontend.serve_forever,
            name="piazza-http-frontend",
            daemon=True,
        )
        http_thread.start()
        # Wait for server to bind (port changes from 0 to actual)
        for _ in range(50):
            _, actual_port = http_frontend.address
            if actual_port != 0:
                break
            time.sleep(0.01)
        actual_host, actual_port = http_frontend.address
        logger.info("HttpFrontend: http://%s:%d", actual_host, actual_port)

    # Start IrcFrontend if requested
    irc_frontend = None
    if args.irc:
        from piazza.frontends.irc import IrcFrontend

        irc_host, irc_port = _parse_host_port(args.irc)
        irc_frontend = IrcFrontend(
            irc_host=irc_host,
            irc_port=irc_port,
            nickname=args.irc_nick,
            channels=args.irc_channels or [],
            use_ssl=args.irc_ssl,
        )
        irc_frontend.attach(bus)
        irc_thread = threading.Thread(
            target=irc_frontend.serve_forever,
            name="piazza-irc-frontend",
            daemon=True,
        )
        irc_thread.start()
        logger.info(
            "IrcFrontend: %s:%d as %s, bridging %s",
            irc_host,
            irc_port,
            args.irc_nick,
            args.irc_channels or "(no channels)",
        )

    # Start AdminServer
    info = bus.start_admin(
        host="0.0.0.0" if args.remote else "127.0.0.1",
        port=args.admin_port,
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
    if irc_frontend:
        irc_frontend.shutdown()
    if http_frontend:
        http_frontend.shutdown()
    bus.close()


def _cmd_client_send(args: argparse.Namespace) -> None:
    """Handle ``client send``.

    Args:
        args: Parsed arguments namespace.
    """
    from piazza.transport_http import HttpTransport

    transport = HttpTransport(args.server, agent_id=args.agent)
    msg_id = transport.publish(args.channel, args.agent, args.msg_type, args.message)
    print(msg_id)
    transport.close()


def _cmd_client_poll(args: argparse.Namespace) -> None:
    """Handle ``client poll``.

    Args:
        args: Parsed arguments namespace.
    """
    from piazza.transport_http import HttpTransport

    transport = HttpTransport(args.server, agent_id=args.agent)

    if args.follow:
        # SSE mode: subscribe and print each message as JSON line
        stop = threading.Event()

        def on_msg(msg):
            print(_msg_to_json(msg), flush=True)

        transport.subscribe(args.channel, on_msg)

        def handle_signal(_signum: int, _frame: object) -> None:
            stop.set()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        stop.wait()
    else:
        # One-shot poll
        for msg in transport.query(args.channel, limit=args.limit):
            print(_msg_to_json(msg))

    transport.close()


def _cmd_client_channels(args: argparse.Namespace) -> None:
    """Handle ``client channels``.

    Args:
        args: Parsed arguments namespace.
    """
    from piazza.transport_http import HttpTransport

    transport = HttpTransport(args.server, agent_id=args.agent)
    for ch in transport.list_channels():
        print(ch)
    transport.close()


def _cmd_client_dm(args: argparse.Namespace) -> None:
    """Handle ``client dm``.

    Args:
        args: Parsed arguments namespace.
    """
    from piazza.transport_http import HttpTransport

    # Compute canonical DM channel (same logic as PiazzaClient._dm_channel)
    pair = sorted([args.agent, args.to_agent])
    channel = f"dm:{pair[0]}:{pair[1]}"

    transport = HttpTransport(args.server, agent_id=args.agent)
    msg_id = transport.publish(channel, args.agent, "chat", args.message)
    print(msg_id)
    transport.close()


# ── Entry Point ───────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    """Entry point for the piazza CLI.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).
    """
    args = parse_args(argv)

    if args.command == "serve":
        _cmd_serve(args)
    elif args.command == "client":
        if args.action is None:
            print("usage: piazza client {send,poll,channels,dm} ...", file=sys.stderr)
            sys.exit(1)
        handlers = {
            "send": _cmd_client_send,
            "poll": _cmd_client_poll,
            "channels": _cmd_client_channels,
            "dm": _cmd_client_dm,
        }
        try:
            handlers[args.action](args)
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            print(f"error: cannot connect to {args.server}: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        _build_parser().print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
