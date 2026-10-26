"""Console entrypoint: `python -m topicforge` and the `topicforge` script."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from topicforge import __version__
from topicforge.config import load_settings
from topicforge.server import build_app
from topicforge.server.http import (
    DEFAULT_HTTP_PORT,
    LOOPBACK_HOST,
    TOKEN_ENV,
    is_loopback,
    serve_http,
    validate_token,
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="topicforge",
        description=(
            "ROS Topic Inspector & Bag Analyzer MCP server. "
            "Runs on stdio by default so MCP clients (Claude Desktop, Claude "
            "Code, etc.) can spawn it directly; --transport streamable-http "
            "serves it on 127.0.0.1 (another --host needs a bearer token)."
        ),
        epilog=(
            "Configuration is read from environment variables: "
            "TOPICFORGE_MODE (mock|live|auto, default auto), "
            "TOPICFORGE_LOG_LEVEL (DEBUG|INFO|WARNING|ERROR, default INFO), "
            "TOPICFORGE_ROS2_BIN (default 'ros2'), "
            f"{TOKEN_ENV} (bearer token for --transport streamable-http, at least 32 "
            "characters; required for a --host other than loopback)."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"topicforge {__version__}",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help=(
            "stdio (default) or streamable-http, an HTTP endpoint bound to "
            f"{LOOPBACK_HOST} by default (reach it through an SSH tunnel)."
        ),
    )
    parser.add_argument(
        "--host",
        default=None,
        help=(
            f"Bind address for --transport streamable-http (default {LOOPBACK_HOST}). "
            f"Anything but loopback requires {TOKEN_ENV}; the traffic is plain HTTP."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"TCP port for --transport streamable-http (default {DEFAULT_HTTP_PORT}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build the MCP app from environment settings and run it on stdio."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    for flag, value in (("--port", args.port), ("--host", args.host)):
        if value is not None and args.transport != "streamable-http":
            parser.error(f"{flag} only applies to --transport streamable-http")
    port = DEFAULT_HTTP_PORT if args.port is None else args.port
    if not 1 <= port <= 65535:
        parser.error("--port must be in 1..65535")
    host = LOOPBACK_HOST if args.host is None else args.host.strip()
    token = os.environ.get(TOKEN_ENV) or None
    if args.transport == "streamable-http":
        if token is not None:
            try:
                validate_token(token)
            except ValueError as exc:
                parser.error(str(exc))
        if not host:
            parser.error("--host must not be empty")
        if token is None and not is_loopback(host):
            parser.error(
                f"--host {host} needs {TOKEN_ENV}: without a token only loopback is served"
            )

    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"topicforge: configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    log = logging.getLogger("topicforge")
    log.info("starting (requested mode=%s)", settings.mode)

    app = build_app(settings)
    try:
        if args.transport == "streamable-http":
            serve_http(app, port, host, token)
        else:
            app.run()
    except KeyboardInterrupt:
        log.info("interrupted by user")
        return 0
    except Exception:
        log.exception("topicforge crashed while serving MCP requests")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
