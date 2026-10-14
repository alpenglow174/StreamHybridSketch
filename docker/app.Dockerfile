FROM python:3.11.9-slim

WORKDIR /app
# Installs from pre-downloaded wheels (docker/wheels) when they are present and
# match this CPU (they are x86-64 manylinux); otherwise falls back to PyPI.
# Offline mode exists because DNS inside Docker Desktop builds can be flaky.
COPY docker/requirements.txt /tmp/requirements.txt
COPY docker/wheels /tmp/wheels
RUN set -e; \
    if [ "$(uname -m)" = "x86_64" ] && ls /tmp/wheels/*.whl >/dev/null 2>&1; then \
        echo ">> installing from bundled wheels (offline)"; \
        pip install --no-cache-dir --no-index --find-links /tmp/wheels -r /tmp/requirements.txt pytest==8.2.2; \
    else \
        echo ">> no usable bundled wheels for $(uname -m): installing from PyPI"; \
        pip install --no-cache-dir --retries 10 --timeout 60 -r /tmp/requirements.txt pytest==8.2.2; \
    fi; \
    rm -rf /tmp/wheels

ENV PYTHONPATH=/app/src:/app \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg
