# syntax=docker/dockerfile:1
# =============================================================================
#  lore-hound — Multi-stage production Dockerfile
#
#  Stage 1 (builder) : install dependencies with uv
#  Stage 2 (runtime) : minimal footprint, non‑root user, production WSGI
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — Install dependencies into a virtual env
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Leverage Docker layer caching: dependencies first, code second
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Stage 2 — Production runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim

# Python runtime hygiene
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE="lorehound.settings"

# Create non‑root user, install only what the app needs at runtime
#   git   → repo_manager clones repositories on the fly
RUN addgroup --system --gid 1001 app && \
    adduser --system --uid 1001 app && \
    apt-get update && \
    apt-get install --no-install-recommends -y git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

# Copy virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code (no .env — excluded by .dockerignore)
COPY . .

# Ensure entrypoint is executable (Windows host files don't carry +x)
RUN chmod +x /app/docker-entrypoint.sh

# Writable directories at runtime
RUN mkdir -p /app/data/repos /app/staticfiles && \
    chown -R app:app /app

USER app

EXPOSE 8000

# Healthcheck — verify the WSGI server is responding
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import http.client as h; c=h.HTTPConnection('localhost',8000); c.request('GET','/admin/'); r=c.getresponse(); r.read(); assert r.status < 500"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "lorehound.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
