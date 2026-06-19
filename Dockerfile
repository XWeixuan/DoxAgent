# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.8.14-python3.11-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --group dev

COPY prompts ./prompts
COPY eval ./eval
COPY examples ./examples
COPY tests ./tests

EXPOSE 8765

CMD ["python", "-m", "doxagent.debug_viewer", "--host", "0.0.0.0", "--port", "8765"]
