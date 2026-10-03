# Builds the network-disabled, digest-pinned VHS environment used by
# ``make docs-terminal-screenshots``. Runtime isolation, non-root execution,
# temporary-state ownership, and verified output publication remain enforced
# by ``scripts/docs_terminal_capture.py``.
ARG UV_IMAGE
ARG VHS_IMAGE

FROM ${UV_IMAGE} AS uv
FROM ${VHS_IMAGE}

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system

COPY . .
RUN /usr/local/bin/uv sync --locked --no-default-groups && \
    test -x /workspace/.venv/bin/ancestry && \
    test -d /usr/lib/locale/C.utf8 && \
    ln -s /usr/lib/locale/C.utf8 /usr/lib/locale/en_US.UTF-8 && \
    install -d -m 0555 /usr/local/lib/ancestryllm-docs-terminal && \
    install -m 0555 scripts/docs_terminal_shell.sh /usr/local/lib/ancestryllm-docs-terminal/bash

ENTRYPOINT []
CMD ["/usr/bin/vhs"]
