# ROS 2 live-adapter bench image. Build context is the repository root.
#   docker build -f tests/integration/ros2/Dockerfile --build-arg DISTRO=humble -t topicforge-ros2:humble .
ARG DISTRO=humble
FROM ros:${DISTRO}
ARG DISTRO

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip python3-venv \
        ros-${DISTRO}-rmw-cyclonedds-cpp \
        ros-${DISTRO}-ros2bag \
        ros-${DISTRO}-rosbag2-storage-default-plugins \
        ros-${DISTRO}-sensor-msgs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/topicforge
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tests ./tests

# A venv keeps TopicForge off the PEP 668 system interpreter (Jazzy) and off
# the old system pip (Humble). The sed strips CRLF from a Windows checkout.
RUN sed -i 's/\r$//' tests/integration/ros2/entrypoint.sh \
    && python3 -m venv /opt/tfenv \
    && /opt/tfenv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/tfenv/bin/pip install --no-cache-dir -e ".[bags,dev]"

ENV TOPICFORGE_BENCH_BAG=/bench/bag
CMD ["bash", "/opt/topicforge/tests/integration/ros2/entrypoint.sh"]
