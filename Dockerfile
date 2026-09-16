FROM node:22-slim AS console
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim-bookworm AS build
RUN pip install --no-cache-dir uv==0.12.5
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 10001 atlas && mkdir -p /app/.local /app/.models && chown -R atlas:atlas /app
WORKDIR /app
COPY --from=build --chown=atlas:atlas /app/.venv /app/.venv
COPY --from=console --chown=atlas:atlas /web/dist /app/web/dist
COPY --chown=atlas:atlas migrations ./migrations
COPY --chown=atlas:atlas scripts ./scripts
COPY --chown=atlas:atlas models ./models
COPY --chown=atlas:atlas datasets ./datasets
COPY --chown=atlas:atlas prompts ./prompts
COPY --chown=atlas:atlas alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH" TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=4
USER atlas
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health')"
CMD ["uvicorn", "atlas.main:app", "--host", "0.0.0.0", "--port", "8100", "--no-access-log", "--timeout-graceful-shutdown", "30"]
