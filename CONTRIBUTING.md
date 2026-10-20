# Contributing

1. Install with `make install` (uv, Python 3.11 or newer).
2. Copy `.env.example` to `.env`. Leave `LLM_PROVIDER=mock` unless you are running a live model.
3. Before a pull request, run `make check`. That is ruff, mypy, and pytest with coverage at or above 80 percent.
4. Every number a user can see needs lineage: `source_id`, `source_file_uri`, `source_row_hash`, and `ingested_at`. Derived numbers also record `method` and `parameters`.
5. Every answer object needs `confidence` and `confidence_reasons`.
6. Do not add CPT descriptor text. Codes only. HCPCS Level II and MS-DRG descriptions from CMS public files are allowed.
7. Do not `json.load()` a hospital machine-readable file. Stream it.
8. LLM calls go through `caretrace/llm/client.py`.
9. Statistical modules expose `explain()` and put the method on the lineage record.

Sample data lives in `data/samples`. `caretrace load-samples` rebuilds the local DuckDB marts.
