# TopicForge observer image for the integration rig.
#
# Installs topicforge from the repo source plus the dds-cyclone extra
# so it can join the same domain as the publisher containers and
# observe their traffic. The scenarios runner inside the maintainer's
# CI driver script invokes TopicForge tools via the MCP CLI ;
# this container just runs the server.

FROM python:3.11-slim AS runtime

WORKDIR /app

COPY pyproject.toml /app/
COPY src /app/src/
COPY README.md /app/

RUN pip install --no-cache-dir -e ".[dds]"

RUN useradd --create-home --uid 1000 observer
USER observer

ENV TOPICFORGE_MODE=live \
    TOPICFORGE_DDS_BACKEND=cyclone \
    TOPICFORGE_DDS_DOMAIN_ID=0

ENTRYPOINT ["python", "-m", "topicforge"]
