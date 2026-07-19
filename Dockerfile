# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.11.16 AS uv

FROM python:3.14-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

RUN groupadd --system roxy \
    && useradd --system --gid roxy --home-dir /app --shell /usr/sbin/nologin roxy \
    && mkdir /app/data \
    && chown -R roxy:roxy /app

USER roxy

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["./scripts/docker-entrypoint.sh"]
