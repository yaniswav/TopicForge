# CycloneDDS integration publisher image.
#
# Base: python:3.11-slim. Installs cyclonedds (BSD-licensed Eclipse
# Foundation binding) and copies the publisher script. ENTRYPOINT
# delegates to `python cyclone_publisher.py` ; the compose service
# passes the per-scenario CLI args as `command`.

FROM python:3.11-slim AS runtime

WORKDIR /app

# CycloneDDS Python binding. Pin the major version line ; bump
# when upstream cuts a tagged release we want to track.
RUN pip install --no-cache-dir "cyclonedds>=0.10"

COPY scripts/integration/publishers/cyclone_publisher.py /app/

# Run as a non-root user — the default in slim is root, which is
# acceptable for a single-purpose throwaway container but cleaner
# to drop down explicitly.
RUN useradd --create-home --uid 1000 publisher
USER publisher

ENTRYPOINT ["python", "/app/cyclone_publisher.py"]
