# API: Python (uv) + local solana-mcp-minimal (Node)
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
  && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
COPY solana-mcp-minimal ./solana-mcp-minimal
RUN cd solana-mcp-minimal && npm install && npm run build
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
  PYTHONUNBUFFERED=1 \
  MCP_ENTRY=/app/solana-mcp-minimal/build/index.js

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
