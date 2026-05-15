# Fast DDS integration publisher image.
#
# Base: python:3.11-slim. Installs fastdds (BSD-licensed eProsima
# binding, pinned to the 2.6.x line per the OSS extras pin).

FROM python:3.11-slim AS runtime

WORKDIR /app

# Fast DDS Python binding — pinned per CHANGELOG / pyproject.toml.
RUN pip install --no-cache-dir "fastdds>=2.6.1,<3"

COPY scripts/integration/publishers/fast_publisher.py /app/

RUN useradd --create-home --uid 1000 publisher
USER publisher

ENTRYPOINT ["python", "/app/fast_publisher.py"]
