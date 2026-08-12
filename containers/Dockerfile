# syntax=docker/dockerfile:1

ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded
ARG PYTHON_IMAGE=python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/ancestryllm

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /source

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-default-groups --no-editable --no-install-project

COPY src ./src
RUN uv sync --locked --no-default-groups --no-editable \
    && /opt/ancestryllm/bin/python -m ancestryllm.container_inventory \
        --output /opt/ancestryllm/package-inventory.json

FROM ${PYTHON_IMAGE} AS runtime

ARG APP_VERSION=0.5.0
LABEL org.opencontainers.image.source="https://github.com/sodejm/AncestryLLM" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.licenses="MIT"

ENV PATH=/opt/ancestryllm/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/ancestryllm /opt/ancestryllm
COPY LICENSE /usr/share/licenses/ancestryllm/LICENSE

USER 65532:65532
WORKDIR /var/lib/ancestryllm

FROM runtime AS gateway

ENTRYPOINT ["/opt/ancestryllm/bin/python", "-m", "ancestryllm.container_gateway"]
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=6 \
    CMD ["/opt/ancestryllm/bin/python", "-m", "ancestryllm.container_healthcheck"]

FROM runtime AS worker

ENTRYPOINT ["/opt/ancestryllm/bin/python", "-m", "ancestryllm.container_worker"]
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=6 \
    CMD ["/opt/ancestryllm/bin/python", "-m", "ancestryllm.container_healthcheck", "--worker"]
