"""Console entrypoint: `python -m topicforge` and the `topicforge` script."""

from __future__ import annotations

import argparse
import logging
import sys

from topicforge import __version__
from topicforge.config import load_settings
from topicforge.server import build_app
from topicforge.server.http import DEFAULT_HTTP_PORT, LOOPBACK_HOST, serve_http


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="topicforge",
        description=(
            "ROS Topic Inspector & Bag Analyzer MCP server. "
            "Runs on stdio by default so MCP clients (Claude Desktop, Claude "
            "Code, etc.) can spawn it directly; --transport streamable-http "
            "serves it on 127.0.0.1 only."
        ),
        epilog=(
            "Configuration is read from environment variables: "
            "TOPICFORGE_MODE (mock|live|auto, default auto), "
            "TOPICFORGE_LOG_LEVEL (DEBUG|INFO|WARNING|ERROR, default INFO), "
            "TOPICFORGE_ROS2_BIN (default 'ros2')."
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
            "stdio (default) or streamable-http, a local HTTP endpoint bound to "
            f"{LOOPBACK_HOST} only (no authentication; reach it through an SSH tunnel)."
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
    if args.port is not None and args.transport != "streamable-http":
        parser.error("--port only applies to --transport streamable-http")
    port = DEFAULT_HTTP_PORT if args.port is None else args.port
    if not 1 <= port <= 65535:
        parser.error("--port must be in 1..65535")

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
            serve_http(app, port)
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
