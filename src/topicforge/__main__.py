"""Console entrypoint: `python -m topicforge` and the `topicforge` script."""

from __future__ import annotations

import argparse
import logging
import sys

from topicforge import __version__
from topicforge.config import load_settings
from topicforge.server import build_app


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="topicforge",
        description=(
            "ROS Topic Inspector & Bag Analyzer MCP server. "
            "Runs on stdio so MCP clients (Claude Desktop, Claude Code, etc.) "
            "can spawn it directly."
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
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build the MCP app from environment settings and run it on stdio."""
    _build_arg_parser().parse_args(argv)

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
    log.info("starting (mode=%s)", settings.effective_mode)

    app = build_app(settings)
    try:
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
