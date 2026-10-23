# TopicForge MCP server image (stdio). Build context is the repository root:
#   docker build -t topicforge .
#   docker run -i --rm --network host topicforge                   # live graph, Linux only
#   docker run -i --rm -e TOPICFORGE_MODE=mock topicforge          # demo robot, any OS
#   docker run -i --rm -v "$PWD/bags:/bags:ro" topicforge          # read your bags
#
# The ROS 2 Jazzy CLI is included so live mode works when the container shares the
# host network (Linux `--network host`, same ROS_DOMAIN_ID). On Docker Desktop
# (Windows, macOS) the container cannot see the host's DDS traffic: live mode then sees
# an empty graph, so use it for mounted bags or the demo robot (TOPICFORGE_MODE=mock).
# Read-only by architecture, like every install.
FROM ros:jazzy-ros-core

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-venv \
        ros-jazzy-rmw-cyclonedds-cpp \
        ros-jazzy-ros2bag \
        ros-jazzy-rosbag2-storage-default-plugins \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/topicforge
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# A venv keeps TopicForge off the PEP 668 system interpreter of Ubuntu 24.04.
RUN python3 -m venv /opt/tfenv \
    && /opt/tfenv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/tfenv/bin/pip install --no-cache-dir . \
    && rm -rf /opt/topicforge/src

# The ros2 CLI writes logs under $HOME/.ros: a home it owns, no root.
RUN useradd --create-home --uid 10001 topicforge
USER topicforge

ENV TOPICFORGE_MODE=auto \
    TOPICFORGE_TELEMETRY=off \
    PYTHONUNBUFFERED=1

# ros_entrypoint.sh sources /opt/ros/jazzy/setup.bash, then runs the server on stdio.
ENTRYPOINT ["/ros_entrypoint.sh", "/opt/tfenv/bin/topicforge"]
