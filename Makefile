PATH := $(HOME)/.local/bin:$(PATH)
export PATH
export DBT_PROFILES_DIR := $(CURDIR)/dbt

.PHONY: install lint type test check ingest dbt api ui eval load-samples format

install:
	uv sync --extra dev

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .
	uv run ruff format --check .

type:
	uv run mypy caretrace

test:
	uv run pytest

check: lint type test

load-samples:
	uv run caretrace load-samples

ingest: load-samples
	uv run caretrace discover || true
	uv run caretrace download || true
	uv run caretrace parse

dbt: load-samples
	cd dbt && uv run dbt deps && uv run dbt build && uv run dbt docs generate

api:
	uv run uvicorn caretrace.api.main:app --host 0.0.0.0 --port 8000

ui:
	uv run streamlit run ui/app.py --server.port 8501 --server.address 0.0.0.0

eval: load-samples
	uv run python -m eval.run
