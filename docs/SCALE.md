# Scale

Payer Transparency-in-Coverage in-network files are often tens of gigabytes. CareTrace does not ship a cluster job in the default install. `caretrace/scale/tic_stream.py` is the reader a PySpark `mapPartitions` task would call on a byte range: `ijson` yields one in-network item at a time and keeps a row only when the NPI and the code are on the allow-lists.

## Local benchmark

`python -m eval.run` writes a 2,000-item synthetic file and streams it. The last run recorded:

| Measure | Value |
| --- | --- |
| File size | 0.324 MB |
| Rows kept | 400 (every 5th item; NPI `1234567893`, code `73721`) |
| Throughput | about 51 MB/s and about 63,000 rows/s |

That number is a single-process read of a tiny file on this machine. It is not a Databricks cost or a GB/min figure for a real payer file. The filter is the part that matters at scale: most of a national file is outside one state's NPIs and the shoppable code list, so the kept table is much smaller than the source.

Delta Lake and Z-ordering on `(code, npi)` are the production layout. Locally the kept rows can be written with delta-rs; the demo stops at the stream so the default environment does not need a Spark runtime.

## Privacy release

Employer views do not show a person's price. `caretrace/scale/privacy.py` clips values, suppresses cells with fewer than 11 records, and adds Laplace noise to a histogram whose L1 sensitivity is 1. Epsilon is spent through `PrivacyAccountant`. The dashboard explanation is `explain_release`: the epsilon used and the noise scale (`1 / epsilon`). A second release that would exceed the budget raises `PrivacyBudgetExceeded`.
