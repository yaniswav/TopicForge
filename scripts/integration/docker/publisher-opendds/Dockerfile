# OpenDDS integration publisher — scaffold image.
#
# OpenDDS does not currently ship a maintained Python binding on
# PyPI. This Dockerfile documents the build-time dependency clearly
# and exits with a clear error when run ; replace with a real
# pyopendds install once upstream stabilizes (tracked in
# `docs/projet-file/mcp-02-spec.md` §11).

FROM python:3.11-slim AS runtime

WORKDIR /app

# BYO OpenDDS Python binding. Until pyopendds is on PyPI the
# maintainer customizes this section per scenario — typically
# building from OpenDDS source + opendds_idl Cython wrappers.
# Uncomment and replace when a real binding ships:
# RUN pip install --no-cache-dir pyopendds>=0.1

COPY scripts/integration/publishers/opendds_publisher.py /app/

RUN useradd --create-home --uid 1000 publisher
USER publisher

ENTRYPOINT ["python", "/app/opendds_publisher.py"]
