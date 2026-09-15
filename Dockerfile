FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 atlas
WORKDIR /app
COPY --from=build /install /usr/local
COPY migrations ./migrations
COPY alembic.ini ./
USER atlas
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health')"
CMD ["uvicorn", "atlas.main:app", "--host", "0.0.0.0", "--port", "8100", "--no-access-log"]
