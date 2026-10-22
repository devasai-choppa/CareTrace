# syntax=docker/dockerfile:1

FROM python:3.11-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
COPY caretrace ./caretrace
RUN uv sync --frozen --no-dev --no-install-project \
    && uv sync --frozen --no-dev

FROM python:3.11-slim AS runtime
WORKDIR /app
RUN useradd --create-home --uid 10001 caretrace
COPY --from=build /app /app
COPY dbt ./dbt
COPY ui ./ui
COPY config ./config
COPY data/samples ./data/samples
ENV PATH="/app/.venv/bin:$PATH" \
    DATA_DIR=/app/data \
    DUCKDB_PATH=/app/data/caretrace.duckdb \
    LLM_PROVIDER=mock \
    DEMO_MODE=true \
    PYTHONUNBUFFERED=1
RUN mkdir -p /app/data && chown -R caretrace:caretrace /app
USER caretrace
EXPOSE 8000 8501
CMD ["uvicorn", "caretrace.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
