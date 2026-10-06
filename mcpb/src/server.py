"""Entry point of the TopicForge MCP Bundle.

The bundle ships no dependencies: `uv run` installs the pinned `topicforge`
package from PyPI (see pyproject.toml) and this file starts it on stdio.
"""

import sys

from topicforge.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
